# 🚀 OCS-CI PR Pulse Dashboard

Real-time Pull Request status tracking and team productivity tool for the [ocs-ci](https://github.com/red-hat-storage/ocs-ci) project.

---

## 📊 View Dashboard

Since this is a **private repository**, the dashboard cannot be hosted on GitHub Pages (which requires a public repo or GitHub Pro). Instead, view the dashboard locally:

### Option 1: Quick View (Recommended)
```bash
# Simply run the view script
python view_dashboard.py
```
This will automatically open the dashboard in your default browser.

### Option 2: Open Directly in Browser
```bash
# Clone the repository (if not already cloned)
git clone https://github.com/PrasadDesala/ocs-ci-pr-pulse.git
cd ocs-ci-pr-pulse

# Open the dashboard in your default browser
# On macOS:
open docs/pr_dashboard.html

# On Linux:
xdg-open docs/pr_dashboard.html

# On Windows:
start docs/pr_dashboard.html
```

### Option 3: Use Python HTTP Server
```bash
# Start a local web server
python -m http.server 8000 --directory docs

# Then open in your browser:
# http://localhost:8000/pr_dashboard.html
```

### Option 4: Generate Demo Dashboard
```bash
# Run the demo dashboard generator (creates sample data)
python generate_demo_dashboard.py

# The script will automatically open the dashboard in your browser
```

---

## Features
- 📊 Real-time PR status tracking
- 🤖 Smart bot filtering
- 👥 Squad-based organization
- 🔍 Advanced search and filtering
- ⏰ Stale PR detection
- 📈 Team productivity metrics


