# Student Performance Dashboard

A dependency-free dashboard for monitoring student scores, attendance, study hours, grades, and risk levels.

## Setup

```powershell
py app.py
```

Open `http://localhost:8501`.

Students are stored in `students.db` in the project folder. Records added through
the dashboard or uploaded by CSV remain after the server stops and restarts.

## Learning materials

Upload revision notes and practice files for assignments, CATs, exams, or general
study. Files are stored in `learning_materials/`, and their details are tracked in
`students.db`.

## Student accounts

Students can create an account or sign in from the dashboard. Registration uses
the student's full name and unique student ID from the roster; students can then
view their own grades and download shared learning materials. Passwords are
stored as salted PBKDF2 hashes, and sign-in sessions expire after seven days.

The sample roster uses IDs `STU-0001` through `STU-0012`. Include a unique
`student_id` column when uploading a CSV so existing accounts remain linked to
the correct student. Keep real student records private and use HTTPS before
deploying this app on a public network.

Students can upload a PNG or JPEG profile picture (up to 5 MB) from their portal.
Pictures are saved in `profile_pictures/` and are only served to the signed-in
account that owns them.

## CSV format

Upload a CSV containing these columns:

- `student_name`
- `student_id`
- `attendance`
- `math`
- `science`
- `english`
- `study_hours`

Column names are case-insensitive and spaces are converted to underscores.
