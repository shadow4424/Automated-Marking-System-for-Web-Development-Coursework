from flask import Flask, render_template_string, request, redirect, url_for, send_file, flash
from pathlib import Path
import shutil
import tempfile
import os
from .pipeline import AssessmentPipeline
from .profiles import PROFILES

app = Flask(__name__)
app.secret_key = "replace-this-with-a-secret-key"

UPLOAD_DIR = Path(tempfile.gettempdir()) / "ams_webui_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Minimal HTML templates inline for brevity
template_upload = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AMS Teacher Marking Portal</title>
</head>
<body>
    <h1>Automated Marking System — Upload Submission</h1>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <ul>
          {% for msg in messages %}
            <li>{{ msg }}</li>
          {% endfor %}
        </ul>
      {% endif %}
    {% endwith %}
    <form method="post" enctype="multipart/form-data">
        <label>Select profile:
            <select name="profile">
            {% for profile in profiles %}
                <option value="{{ profile }}">{{ profile|capitalize }}</option>
            {% endfor %}
            </select>
        </label><br><br>
        <label>Submission ZIP or folder:
            <input type="file" name="submission" webkitdirectory directory multiple required/>
        </label><br><br>
        <button type="submit">Upload & Mark</button>
    </form>
</body>
</html>
"""

template_report = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>AMS Mark Report</title>
</head>
<body>
    <h1>Report for: {{ meta['submission_name'] }}</h1>
    <h2>Profile: {{ profile }}</h2>
    <h3>Scores</h3>
    <pre>{{ scores | tojson(indent=2) }}</pre>
    <h3>Findings</h3>
    <ul>
    {% for f in findings %}
        <li>[{{ f['category'] }}] {{ f['message'] }} (Severity: {{ f['severity'] }})</li>
    {% endfor %}
    </ul>
    <a href="{{ url_for('upload') }}">Back to Upload</a>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        profile = request.form.get("profile", "frontend")
        submission_files = request.files.getlist("submission")
        if not submission_files:
            flash("No files uploaded.")
            return redirect(request.url)
        # Save submission files to a temp dir
        sub_dir = UPLOAD_DIR / next(tempfile._get_candidate_names())
        sub_dir.mkdir(parents=True, exist_ok=True)
        for file in submission_files:
            # Chrome submits folder structure in filename
            dst = sub_dir / file.filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            file.save(dst)
        # Run the pipeline
        workspace = UPLOAD_DIR / ("workspace_" + next(tempfile._get_candidate_names()))
        workspace.mkdir(parents=True, exist_ok=True)
        pipeline = AssessmentPipeline()
        # Pass profile into pipeline.run (new argument)
        report_path = pipeline.run(submission_path=sub_dir, workspace_path=workspace, profile=profile)
        return redirect(url_for("report", report_file=report_path))
    return render_template_string(template_upload, profiles=PROFILES.keys())

@app.route("/report")
def report():
    report_file = request.args.get("report_file")
    if not report_file or not Path(report_file).exists():
        return "Report not found", 404
    import json
    with open(report_file, encoding='utf-8') as f:
        report_data = json.load(f)
    return render_template_string(
        template_report,
        meta=report_data['metadata'],
        profile=report_data.get('metadata', {}).get('profile', 'unknown'),
        scores=report_data['scores'],
        findings=report_data['findings']
    )

if __name__ == "__main__":
    app.run(debug=True, port=5000)

