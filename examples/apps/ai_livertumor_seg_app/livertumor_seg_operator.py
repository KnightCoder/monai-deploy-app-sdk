# Copyright 2021-2023 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
from pathlib import Path

from numpy import uint8

from monai.deploy.core import ConditionType, Fragment, Operator, OperatorSpec
from monai.deploy.operators.monai_seg_inference_operator import InMemImageReader, MonaiSegInferenceOperator
from monai.transforms import (
    Activationsd,
    AsDiscreted,
    Compose,
    CropForegroundd,
    EnsureChannelFirstd,
    Invertd,
    LoadImaged,
    SaveImaged,
    ScaleIntensityRanged,
    Spacingd,
)


# @md.env(pip_packages=["monai>=1.0.0", "torch>=1.5", "numpy>=1.21", "nibabel"])
class LiverTumorSegOperator(Operator):
    """Performs liver and tumor segmentation using a DL model with an image converted from a DICOM CT series.

    The model used in this application is from NVIDIA, publicly available at
    https://ngc.nvidia.com/catalog/models/nvidia:med:clara_pt_liver_and_tumor_ct_segmentation

    Described in the downloaded model package, also called Medical Model Archive (MMAR), are the pre and post
    transforms and inference configurations. The MONAI Core transforms are used, as such, these transforms are
    simply ported to this operator, while changing SegmentationSaver "handler" to SaveImageD post "transform".

    This operator makes use of the App SDK MonaiSegInferenceOperator in a composition approach.
    It creates the pre-transforms as well as post-transforms with MONAI dictionary based transforms.
    Note that the App SDK InMemImageReader, derived from MONAI ImageReader, is passed to LoadImaged.
    This derived reader is needed to parse the in memory image object, and return the expected data structure.
    Loading of the model, and predicting using the in-proc PyTorch inference is done by MonaiSegInferenceOperator.

    Named Input:
        image: Image object.

    Named Outputs:
        seg_image: Image object of the segmentation object.
        saved_images_folder: Path to the folder with intermediate image output, not requiring a downstream receiver.
    """

    DEFAULT_OUTPUT_FOLDER = Path.cwd() / "output/saved_images_folder"

    def __init__(
        self, frament: Fragment, *args, model_path: Path, output_folder: Path = DEFAULT_OUTPUT_FOLDER, **kwargs
    ):
        self.logger = logging.getLogger("{}.{}".format(__name__, type(self).__name__))
        self._input_dataset_key = "image"
        self._pred_dataset_key = "pred"

        self.model_path = model_path
        self.output_folder = output_folder
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.fragement = frament  # Cache and later pass the Fragment/Application to contained operator(s)
        self.input_name_image = "image"
        self.output_name_seg = "seg_image"
        self.output_name_saved_images_folder = "saved_images_folder"

        self.fragement = frament  # Cache and later pass the Fragment/Application to contained operator(s)
        super().__init__(frament, *args, **kwargs)

    def setup(self, spec: OperatorSpec):
        spec.input(self.input_name_image)
        spec.output(self.output_name_seg)
        spec.output(self.output_name_saved_images_folder).condition(
            ConditionType.NONE
        )  # Output not requiring a receiver

    def compute(self, op_input, op_output, context):
        input_image = op_input.receive(self.input_name_image)
        if not input_image:
            raise ValueError("Input image is not found.")

        # This operator gets an in-memory Image object, so a specialized ImageReader is needed.
        _reader = InMemImageReader(input_image)

        # In this example, the input image, once loaded at the beginning of the pre-transforms, can
        # be saved on disk, so can the segmentation prediction image at the end of the post-transform.
        # They are both saved in the same subfolder of the application output folder, with names
        # distinguished by the postfix. They can also be saved in different subfolder if need be.
        # These images files can then be packaged for rendering.
        # In the code below, saving of the image files are disabled to save 10 seconds if nii, and 20 if nii.gz
        pre_transforms = self.pre_process(_reader, str(self.output_folder))
        post_transforms = self.post_process(pre_transforms, str(self.output_folder))

        # Delegates inference and saving output to the built-in operator.
        infer_operator = MonaiSegInferenceOperator(
            self.fragement,
            roi_size=(
                160,
                160,
                160,
            ),
            pre_transforms=pre_transforms,
            post_transforms=post_transforms,
            overlap=0.6,
            model_name="",
            model_path=self.model_path,
        )

        # Setting the keys used in the dictionary based transforms
        infer_operator.input_dataset_key = self._input_dataset_key
        infer_operator.pred_dataset_key = self._pred_dataset_key

        # Now emit data to the output ports of this operator
        op_output.emit(infer_operator.compute_impl(input_image, context), self.output_name_seg)
        op_output.emit(self.output_folder, self.output_name_saved_images_folder)

    def pre_process(self, img_reader, out_dir: str = "./input_images") -> Compose:
        """Composes transforms for preprocessing input before predicting on a model."""

        Path(out_dir).mkdir(parents=True, exist_ok=True)

        my_key = self._input_dataset_key
        return Compose(
            [
                LoadImaged(keys=my_key, reader=img_reader),
                EnsureChannelFirstd(keys=my_key),
                # The SaveImaged transform can be commented out to save 5 seconds.
                # Uncompress NIfTI file, nii, is used favoring speed over size, but can be changed to nii.gz
                # SaveImaged(
                #     keys=my_key,
                #     output_dir=out_dir,
                #     output_postfix="",
                #     resample=False,
                #     output_ext=".nii",
                # ),
                Spacingd(keys=my_key, pixdim=(1.0, 1.0, 1.0), mode=("bilinear"), align_corners=True),
                ScaleIntensityRanged(my_key, a_min=-21, a_max=189, b_min=0.0, b_max=1.0, clip=True),
                CropForegroundd(my_key, source_key=my_key),
            ]
        )

    def post_process(self, pre_transforms: Compose, out_dir: str = "./prediction_output") -> Compose:
        """Composes transforms for postprocessing the prediction results."""

        Path(out_dir).mkdir(parents=True, exist_ok=True)

        pred_key = self._pred_dataset_key
        return Compose(
            [
                Activationsd(keys=pred_key, softmax=True),
                AsDiscreted(keys=pred_key, argmax=True),
                Invertd(
                    keys=pred_key, transform=pre_transforms, orig_keys=self._input_dataset_key, nearest_interp=True
                ),
                # The SaveImaged transform can be commented out to save 5 seconds.
                # Uncompress NIfTI file, nii, is used favoring speed over size, but can be changed to nii.gz
                # SaveImaged(
                #     keys=pred_key,
                #     output_dir=out_dir,
                #     output_postfix="seg",
                #     output_dtype=uint8,
                #     resample=False,
                #     output_ext=".nii",
                # ),
            ]
        )
