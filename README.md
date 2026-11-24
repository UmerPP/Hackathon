# ReportFix

Python campus issue tracker with separate Student, Department staff, and Registrar sign-in pages. A new database starts with zero accounts and zero reports.

## Start on Windows

Extract the whole ZIP. Run **start.bat**, then open http://127.0.0.1:5080. Python 3.11 or later is required. The first launch installs dependencies and creates usersecrets.json from the example when needed. For subsequent starts, you can also run `.\.venv\Scripts\python.exe app.py`.

## Set up accounts

- **Students** create their own account using an email address and password. The first sign-in uses only their password. Once signed in, they scan the authenticator QR and continue to the desk. Later sign-ins ask for the six-digit authenticator code after the password. The registrar approves student accounts under **Account approvals**.
- **First registrar**. Create an account on the Registrar page using your email and password, then sign in and scan the QR. From the application directory, run `start.bat --approve-registrar YOUR_EMAIL` and check access status on the pending page.
- **Department staff**. Create an account on the staff page and complete first sign-in. The registrar assigns a department under **Account approvals**. Staff see only their assigned queue.
- **Email alerts**. Staff and the registrar verify the email address they registered with from their desk. Email verification is separate from QR authentication so sign-in can work while SMTP is unavailable. After signing out, wait for a fresh authenticator code before signing back in.

## Configure Gmail and Gemini

Edit **usersecrets.json** locally. In the shareable ZIP, use **usersecrets.example.json** as a reference.

- `smtp_user` and `smtp_app_password` are the Gmail sender and its app password. ReportFix authenticates at smtp.gmail.com on port 465 over TLS. If Gmail rejects the credentials, create a new app password in the same Google Account as the configured sender and replace `smtp_app_password` in the local file. ReportFix reads the updated settings on the next send.
- `department_emails` can optionally set a specific mailbox for a department or Registrar. Leave blank to use the verified addresses of accounts registered for that department or the registrar role.
- `alert_window_hours` defaults to 24. A matching department, canonical service, and campus location share one alert in that window, even when a report reopens the concern. The alert job is committed with the report. The registrar sees pending, sent, and uncertain delivery states. A pending job retries when configuration or transport becomes available. Ambiguous deliveries are marked uncertain to avoid sending twice.
- `gemini_api_key` and `gemini_model` configure categorization. The app validates Gemini's output against its category list and asks for clarification when needed. A local classifier supplies a suggestion during service outages. Reports already identified as confidential by local screening or the student's checkbox are classified locally. Other report narratives go to Gemini for categorization.

Configure the campus registry and routing defaults in **triage.py**. Reports require a registered campus location. Students can choose the concerned department during review, and confidential cases always go to the registrar. Alert emails contain case references. Confidential alerts omit the report narrative and location.

## Try the complete flow

1. Register two student accounts and a department staff account. From the registrar desk, approve both students and assign the staff member to Cafeteria. Verify the staff email address to enable delivery.
2. From Student, submit **Cafeteria toaster kharab hai** and select Cafeteria. Review the category, place and department before submitting. The confirmation appears as a dismissible toast.
3. From a second student, submit **The canteen bread toaster stopped working** for the same place and department. Check that both students track one case and that the registrar sees one alert.
4. From Cafeteria staff, start work and mark the case resolved. Only the affected roster at resolution can vote. Credible disputes can reopen it, and registrar inspection updates the trust ledger.
5. Try a calm safety report, an unknown campus location, and a confidential ragging report to verify priority, clarification and restricted routing. Each desk lists active cases before completed cases, then sorts by priority, affected students, and newest case.

Run tests with `.\.venv\Scripts\python.exe -m unittest -v`. The suite uses temporary SQLite databases and simulated external responses. The local application listens on loopback. Public deployment would need a production HTTPS setup.
