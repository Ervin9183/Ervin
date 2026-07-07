from pathlib import Path
import runpy


APP_PATH = Path(__file__).parent / "NESO BSAD Requirement (Streamlit).py"

runpy.run_path(APP_PATH, run_name="__main__")
