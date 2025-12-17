[![GitHub Org](https://img.shields.io/badge/GitHub-HESCOR-blue?logo=github&logoColor=white)](https://github.com/HESCOR)
# Survey123 to PDF Exporter

This project provides a command-line workflow for turning a Survey123 CSV export into a set of A4 PDF summaries. It was designed around the following requirements:

* Interpret the CSV column headers as survey questions.
* Generate one PDF per survey response (CSV row), alternating between question and answer text.
* Group repeated question blocks (e.g., file attachments 1-5) and omit unanswered questions.
* Allow selecting a subset of rows to render while defaulting to all responses.

The repository includes a ready-to-run Python script, an isolated virtual environment setup, and documentation to help you get started quickly. The exporter ships with the DejaVu Sans font embedded so it can render a wide range of Unicode characters without any additional setup.

## Repository Layout

```
.
├── LICENSE
├── README.md
├── requirements.txt
├── setup.sh
├── survey123_to_pdf.py
└── out_pdfs/
    └── .gitkeep
```

* `survey123_to_pdf.py` — CLI script that builds PDFs from a Survey123 CSV export using pandas and ReportLab.
* `setup.sh` — Convenience script to create a Python virtual environment and install dependencies.
* `requirements.txt` — Python dependencies required by the script.
* `out_pdfs/` — Default output directory for generated PDFs (tracked only for structure).

## Prerequisites

* Python 3.8 or newer.
* The ability to build Python wheels (ReportLab may require system build tools such as `libjpeg-dev`, `zlib1g-dev`, or Xcode command line tools depending on your OS).

## Quick Setup

Run the provided setup script to create a local virtual environment and install dependencies:

```bash
./setup.sh
```

The script will:

1. Create (or reuse) a `venv/` virtual environment in the project root.
2. Upgrade `pip` inside the environment.
3. Install the packages listed in `requirements.txt`.

> **Note:** The environment is activated only within the script while it runs. After setup completes, activate it in your shell before running the exporter:
>
> ```bash
> source venv/bin/activate
> ```

To deactivate the environment later, run `deactivate`.

## Manual Setup (Alternative)

If you prefer to manage dependencies yourself:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Usage

1. Export your Survey123 responses as a CSV file.
2. Activate the virtual environment (`source venv/bin/activate`).
3. Run the exporter:

```bash
python survey123_to_pdf.py path/to/export.csv -o out_pdfs
```

By default, the script renders every row in the CSV. Use the following option to tailor the output:

* `--rows 0,2,5-7` — Render specific row indexes or index ranges.

Each PDF is named with a slugified version of the chosen title column (falling back to `row_<index>` when no title is present) and is written to the directory specified by `-o/--outdir` (defaults to `out_pdfs/`).

## Output Structure

Within each PDF, questions and answers are presented as alternating paragraphs. Repeated column groups (e.g., `Attachment`, `Attachment.1`, …) are automatically detected and laid out in labeled sections such as “File 1”, “File 2”, etc. Empty answers are skipped so that only relevant information is included.

## Sample Workflow

```bash
source venv/bin/activate
python survey123_to_pdf.py survey_export.csv -o out_pdfs
open out_pdfs/My_First_Submission.pdf  # macOS example
```

## Troubleshooting

* **Missing glyphs:** The bundled DejaVu Sans font should cover most characters. If you encounter gaps, you can modify `survey123_to_pdf.py` to register an additional font.
* **ReportLab build errors:** Install system build prerequisites (e.g., `sudo apt-get install libjpeg-dev zlib1g-dev` on Debian/Ubuntu).
* **No rows selected:** Ensure your `--rows` filters match data in the CSV.

## License

This project is distributed under the terms of the MIT License. See [LICENSE](LICENSE) for details.
