# Copyright 2021 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


class Status:
    """Status of a container."""

    def __init__(self, status_code: int, message: str):
        self.status_code: int = status_code
        self.message: str = message

    def __str__(self):
        return f'({self.status_code}, "{self.message}")'


NOT_STARTED = Status(-1, "Not started")
