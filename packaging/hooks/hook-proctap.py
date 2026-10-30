"""Bundle the Windows process-capture implementation, not optional OS backends."""
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

hiddenimports = ["proctap._native", "proctap.backends.windows"]
datas = collect_data_files("proctap") + copy_metadata("proc-tap")
