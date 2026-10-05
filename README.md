# 🚀 OCS-CI PR Pulse Dashboard

An intelligent Pull Request management dashboard for the [ocs-ci](https://github.com/red-hat-storage/ocs-ci) project, featuring real-time PR tracking, smart workload distribution, and team productivity analytics.

---

## 📊 View Dashboard

**Live Dashboard:** [https://prasaddesala.github.io/ocs-ci-pr-pulse/pr_dashboard.html](https://prasaddesala.github.io/ocs-ci-pr-pulse/pr_dashboard.html)

Or view locally:

```bash
# Clone the repository
git clone https://github.com/PrasadDesala/ocs-ci-pr-pulse.git
cd ocs-ci-pr-pulse

# Open in browser
open docs/pr_dashboard.html
```

---

## ✨ Key Features

### 📊 **Real-Time PR Tracking**
- Live status updates from GitHub API
- Automatic detection of PRs waiting for review
- Smart bot filtering (excludes CI bots, only shows human reviews)

### 🎯 **Smart PR Assignment Algorithm**
- **Weighted Load Balancing**: PR sizes weighted (XS=0.5, S=1, M=2, L=3, XL=5) for fair distribution
- **Dynamic Thresholds**: Automatically calculates team capacity (Heavy = 1.2× avg, Overloaded = 1.5× avg)
- **Squad Expertise Matching**: Assigns based on reviewer's squad experience
- **Author Exclusion**: Prevents self-review assignments
- **Visual Load Indicators**: 🟢 OK, 🟡 Heavy, 🔴 Overloaded, ⚪ Idle

### 👥 **Team Workload View**
- Real-time workload status for team members
- Pending PRs with clickable links
- Squad expertise breakdown with visual indicators
- Color-coded status badges for quick scanning

### 📈 **Trends & Analytics**
- Weekly PR activity charts with date ranges
- PR age tracking and highlighting
- Team productivity metrics over time
- Review velocity analysis

### 🎨 **Modern UI/UX**
- Color-coded PR size badges (XS/S/M/L/XL)
- Squad color variety (each squad has unique color)
- Loading states with button feedback
- Responsive design for all screen sizes
- Empty state messaging for better user guidance

---

## 🚀 Quick Start

### Prerequisites
- Python 3.7+
- GitHub Personal Access Token with `repo` scope

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/PrasadDesala/ocs-ci-pr-pulse.git
   cd ocs-ci-pr-pulse
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set environment variables**
   ```bash
   export GITHUB_TOKEN="your_github_token"
   export REPO_NAME="red-hat-storage/ocs-ci"
   ```

4. **Generate the dashboard**
   ```bash
   python3 .github/scripts/generate_dashboard.py
   ```

5. **View the dashboard**
   ```bash
   open docs/pr_dashboard.html
   ```

---

## 📁 Project Structure

```
ocs-ci-pr-pulse/
├── .github/
│   ├── scripts/
│   │   └── generate_dashboard.py    # Main dashboard generator with inline Jinja2 template
│   └── workflows/
│       └── pr_dashboard.yml          # GitHub Actions workflow (runs daily at 8 AM IST)
├── docs/
│   ├── pr_dashboard.html             # Generated dashboard (do not edit directly)
│   └── dashboard_data.json           # PR data from GitHub API
└── README.md
```

---

## 🔧 Configuration

### GitHub Actions Auto-Update

The dashboard updates automatically every day at **8:00 AM IST** via GitHub Actions.

**Workflow:** `.github/workflows/pr_dashboard.yml`

To modify the schedule:
```yaml
schedule:
  - cron: '30 2 * * *'  # 8:00 AM IST (2:30 AM UTC)
```

### Squad Mapping

Team members are organized by squads defined in `.github/scripts/generate_dashboard.py`:

```python
SQUAD_MAPPING = {
    'magenta': ['user1', 'user2', 'user3', ...],
    'red': ['user4', 'user5', 'user6', ...],
    'black': ['user7', 'user8', 'user9', ...],
    'blue': ['user10', 'user11', ...],
    'brown': ['user12', 'user13', ...],
    'purple': ['user14', 'user15', ...],
    'green': ['user16', 'user17', ...],
    'general': ['user18', ...]
}
```

---

## 🎯 Smart PR Assignment

The algorithm considers multiple factors:

1. **Current Workload**: Weighted by PR size (XL = 5× an XS PR)
2. **Squad Expertise**: Prioritizes reviewers familiar with the squad
3. **Fair Distribution**: Balances load across team members
4. **Dynamic Capacity**: Adapts to team's current bandwidth

**Example:**
```
Input: 3 PRs to assign (2× XL, 1× S)
Output:
  - Reviewer1: 1 XL PR (load: 5) - has magenta squad experience
  - Reviewer2: 1 S PR (load: 1) - was idle
  - Reviewer3: 1 XL PR (load: 5) - balanced distribution
```

---

## 📊 Dashboard Sections

### 1. **Overview Cards**
- Total open PRs
- PRs waiting for review
- Average load per person
- Overloaded reviewers count

### 2. **Team Workload Table**
- Member name with status badge
- Assigned PRs count
- Reviewed PRs count
- Pending PRs (with clickable links)
- Squad expertise breakdown

### 3. **Smart PR Assignment**
- Filter by squad/size/age
- One-click assignment suggestions
- Visual load indicators
- Weighted load calculations

### 4. **Trends & Analytics**
- Weekly activity charts with date ranges
- PR status distribution
- Historical trends

---

## 🛠️ Development

### Regenerate Dashboard Locally

```bash
# Set your GitHub token
export GITHUB_TOKEN="ghp_your_token_here"
export REPO_NAME="red-hat-storage/ocs-ci"

# Run the generator
python3 .github/scripts/generate_dashboard.py

# View changes
open docs/pr_dashboard.html
```

### Template Customization

All UI fixes are in the **Jinja2 template** (lines 388-2323) inside `generate_dashboard.py`.

**Important:** Never edit `docs/pr_dashboard.html` directly - changes will be overwritten. Edit the template in the Python script instead.

---

## 🔗 Links

- **Live Dashboard**: [View Dashboard](https://prasaddesala.github.io/ocs-ci-pr-pulse/pr_dashboard.html)
- **Source Repository**: [OCS-CI Project](https://github.com/red-hat-storage/ocs-ci)
- **Report Issues**: [Issue Tracker](https://github.com/PrasadDesala/ocs-ci-pr-pulse/issues)


