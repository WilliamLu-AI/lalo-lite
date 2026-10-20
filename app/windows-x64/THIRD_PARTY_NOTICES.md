# Third-party notices

Bundled Noto Sans SC and Noto Serif SC font files in web/fonts/ are covered by the SIL Open Font License 1.1. Their copyright and full license notices are included in OFL-notosanssc.txt and OFL-notoserifsc.txt in that directory.

Smart Turn v3.2 CPU is downloaded separately by `lalo init --download-model`. Its BSD 2-Clause license is included in models/LICENSE.smart-turn; the source revision and checksums are recorded in models/model.lock.json. Model weights are not bundled in the application archive.

JavaScript and Python dependencies retain their own licenses. Binary previews include collected license notices in the licenses/ directory, including Python, Marked, DOMPurify, and Nuitka's runtime exception. The exception permits proprietary compiled applications; it does not change the licenses of third-party dependencies.

The NumPy speech frontend follows the Whisper log-mel feature convention. Synthetic regression fixtures were generated with Hugging Face Transformers to verify numerical equivalence; Transformers is not bundled in the application.
