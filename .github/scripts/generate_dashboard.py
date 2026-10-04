#!/usr/bin/env python3
"""
PR Dashboard Generator
Generates an interactive HTML dashboard showing PR status across squads
"""

import os
import json
import threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from github import Github, Auth
from jinja2 import Environment
from markupsafe import Markup

# Squad mapping from CODEOWNERS
SQUAD_MAPPING = {
    'turquoise-squad': ['tests/functional/disaster-recovery/'],
    'aqua-squad': ['tests/lvmo/'],
    'brown-squad': [
        'tests/cross_functional/resilience/',
        'tests/functional/nfs_feature/',
        'tests/functional/odf-cli/',
        'tests/functional/pod_and_daemons/',
        'tests/functional/z_cluster/'
    ],
    'green-squad': [
        'tests/functional/pv/pv_services/',
        'tests/functional/storageclass/',
        'tests/functional/encryption/'
    ],
    'blue-squad': ['tests/functional/monitoring/'],
    'red-squad': [
        'tests/functional/object/mcg/',
        'tests/functional/object/rgw/'
    ],
    'purple-squad': ['tests/functional/upgrade/'],
    'magenta-squad': [
        'tests/functional/workloads/',
        'tests/cross_functional/flowtest/',
        'tests/cross_functional/kcs/',
        'tests/cross_functional/longevity/',
        'tests/cross_functional/system_test/'
    ],
    'grey-squad': ['tests/cross_functional/performance/'],
    'orange-squad': ['tests/cross_functional/scale/'],
    'black-squad': [
        'tests/functional/ui/',
        'tests/cross_functional/ui/',
        'ocs_ci/ocs/ui/'
    ]
}


def get_pr_age_days(pr):
    """Calculate PR age in days"""
    created_at = pr.created_at
    # Make now timezone-aware to match GitHub's timezone-aware datetime
    now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.utcnow()
    age = now - created_at
    return age.days


def get_pr_status(pr):
    """Determine if PR is waiting on reviewer or author.
    Returns (status, list_of_human_reviewer_logins).
    """
    reviews = list(pr.get_reviews())

    if not reviews:
        return 'waiting_reviewer', []

    BOT_USERNAMES = ['ocs-ci', 'openshift-ci', 'dependabot', 'renovate']

    human_reviews = [
        r for r in reviews
        if not (
            '[bot]' in r.user.login.lower()
            or r.user.type == 'Bot'
            or r.user.login in BOT_USERNAMES
        )
        and r.user.login != pr.user.login
    ]

    reviewer_logins = list(set(r.user.login for r in human_reviews))

    if not human_reviews:
        return 'waiting_reviewer', reviewer_logins

    changes_requested_reviews = [r for r in human_reviews if r.state == 'CHANGES_REQUESTED']
    approved_reviews = [r for r in human_reviews if r.state == 'APPROVED']
    commented_reviews = [r for r in human_reviews if r.state == 'COMMENTED']

    commits = list(pr.get_commits())
    latest_commit = commits[-1] if commits else None

    if changes_requested_reviews:
        latest_change_request = changes_requested_reviews[-1]
        if latest_commit and latest_commit.commit.author.date > latest_change_request.submitted_at:
            return 'waiting_reviewer', reviewer_logins
        return 'waiting_author', reviewer_logins

    elif commented_reviews:
        latest_comment_review = commented_reviews[-1]
        has_body = bool(latest_comment_review.body)
        has_inline_comments = False
        if pr.review_comments > 0:
            review_comments = list(pr.get_review_comments())
            human_inline_comments = [
                c for c in review_comments
                if not (
                    '[bot]' in c.user.login.lower()
                    or c.user.type == 'Bot'
                    or c.user.login in BOT_USERNAMES
                )
            ]
            has_inline_comments = len(human_inline_comments) > 0

        if has_body or has_inline_comments:
            if latest_commit and latest_commit.commit.author.date > latest_comment_review.submitted_at:
                return 'waiting_reviewer', reviewer_logins
            return 'waiting_author', reviewer_logins

    if approved_reviews:
        return 'approved', reviewer_logins

    return 'waiting_reviewer', reviewer_logins


def is_draft_or_wip(pr):
    """Check if PR is a draft or has WIP/Draft in title"""
    if pr.draft:
        return True
    
    # Check title for WIP/Draft indicators
    title_lower = pr.title.lower()
    wip_indicators = ['wip', 'draft', '[wip]', '[draft]', 'work in progress']
    
    return any(indicator in title_lower for indicator in wip_indicators)


def determine_squad(pr, repo):
    """Determine which squad owns this PR based on changed files"""
    files = list(pr.get_files())
    file_paths = [f.filename for f in files]
    
    squad_matches = defaultdict(int)
    
    for file_path in file_paths:
        for squad, patterns in SQUAD_MAPPING.items():
            for pattern in patterns:
                if file_path.startswith(pattern):
                    squad_matches[squad] += 1
    
    if squad_matches:
        # Return squad with most file matches, remove '-squad' suffix for display
        squad_name = max(squad_matches.items(), key=lambda x: x[1])[0]
        return squad_name.replace('-squad', '')
    
    return 'general'


def is_stale(pr, days=7):
    """Check if PR is stale (no activity for X days)"""
    updated_at = pr.updated_at
    # Make now timezone-aware to match GitHub's timezone-aware datetime
    now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.utcnow()
    age = now - updated_at
    return age.days >= days


def get_pr_size(pr):
    """Calculate PR size based on additions + deletions"""
    total_changes = pr.additions + pr.deletions
    
    if total_changes < 100:
        return 'XS'
    elif total_changes < 300:
        return 'S'
    elif total_changes < 500:
        return 'M'
    elif total_changes < 1000:
        return 'L'
    else:
        return 'XL'


DEFAULT_TEAM_MEMBERS = []


def build_reviewer_profiles(pr_data):
    """Build reviewer profiles for ALL users who appear in PR reviews/assignments."""
    profiles = {}

    all_prs = [pr for prs in pr_data['by_squad'].values() for pr in prs]

    all_authors = set()
    for pr in all_prs:
        all_authors.add(pr['author'])

        # Track all reviewers involved with this open PR (requested OR already reviewed)
        all_reviewers_on_pr = set(pr.get('reviewers', []) + pr.get('actual_reviewers', []))

        for reviewer in all_reviewers_on_pr:
            if reviewer not in profiles:
                profiles[reviewer] = {
                    'login': reviewer,
                    'open_reviews': 0,
                    'total_reviews': 0,
                    'squad_expertise': defaultdict(int),
                    'open_pr_numbers': [],
                }
            p = profiles[reviewer]
            p['open_reviews'] += 1
            p['open_pr_numbers'].append(pr['number'])
            p['squad_expertise'][pr['squad']] += 1

        # Also count total reviews submitted (for expertise weight)
        for reviewer in pr.get('actual_reviewers', []):
            if reviewer not in profiles:
                profiles[reviewer] = {
                    'login': reviewer,
                    'open_reviews': 0,
                    'total_reviews': 0,
                    'squad_expertise': defaultdict(int),
                    'open_pr_numbers': [],
                }
            profiles[reviewer]['total_reviews'] += 1

    # Ensure all authors have a profile (even if they never reviewed anything)
    for author in all_authors:
        if author not in profiles:
            profiles[author] = {
                'login': author,
                'open_reviews': 0,
                'total_reviews': 0,
                'squad_expertise': defaultdict(int),
                'open_pr_numbers': [],
            }

    for login, profile in profiles.items():
        if profile['open_pr_numbers']:
            ages = [pr['age_days'] for pr in all_prs if pr['number'] in profile['open_pr_numbers']]
            profile['avg_review_age'] = round(sum(ages) / len(ages), 1) if ages else 0

    result = {}
    for login, profile in profiles.items():
        profile['squad_expertise'] = dict(profile['squad_expertise'])
        result[login] = profile

    return result


def collect_pr_data(repo_name, token):
    """Collect all PR data from GitHub"""
    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo = g.get_repo(repo_name)
    
    # Get all open PRs
    print("Fetching open PRs...")
    open_prs = list(repo.get_pulls(state='open', sort='created', direction='desc'))
    total_prs = len(open_prs)
    print(f"Found {total_prs} open PRs (including drafts)")
    
    pr_data = {
        'by_squad': defaultdict(list),
        'by_status': defaultdict(list),
        'stale_prs': [],
        'draft_prs': [],
        'all_labels': set(),
        'all_branches': set(),
        'summary': {
            'total_open': 0,
            'waiting_reviewer': 0,
            'waiting_author': 0,
            'approved': 0,
            'stale': 0,
            'draft': 0,
            'avg_age_days': 0
        },
        'generated_at': datetime.now(timezone.utc).isoformat()
    }
    
    processed_count = [0]
    lock = threading.Lock()

    def process_single_pr(idx, pr):
        age_days = get_pr_age_days(pr)
        status, actual_reviewers = get_pr_status(pr)
        squad = determine_squad(pr, repo)
        stale = is_stale(pr)
        size = get_pr_size(pr)
        draft = is_draft_or_wip(pr)
        label_names = [l.name for l in pr.labels]

        pr_info = {
            'number': pr.number,
            'title': pr.title,
            'author': pr.user.login,
            'url': pr.html_url,
            'created_at': pr.created_at.isoformat(),
            'updated_at': pr.updated_at.isoformat(),
            'age_days': age_days,
            'status': status,
            'squad': squad,
            'is_stale': stale,
            'is_draft': draft,
            'size': size,
            'additions': pr.additions,
            'deletions': pr.deletions,
            'reviewers': [r.login for r in pr.requested_reviewers],
            'actual_reviewers': actual_reviewers,
            'labels': label_names,
            'comments': pr.comments,
            'review_comments': pr.review_comments,
            'base_branch': pr.base.ref,
            'is_verified': 'Verified' in label_names
        }

        with lock:
            processed_count[0] += 1
            if processed_count[0] % 10 == 0 or processed_count[0] == 1:
                print(f"Processing PR {processed_count[0]}/{total_prs} (#{pr.number})...")

        return pr_info

    print(f"Processing PRs with 4 threads...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(process_single_pr, idx, pr): pr
            for idx, pr in enumerate(open_prs, 1)
        }
        for future in as_completed(futures):
            pr_info = future.result()

            for label in pr_info['labels']:
                pr_data['all_labels'].add(label)
            pr_data['all_branches'].add(pr_info['base_branch'])

            pr_data['by_squad'][pr_info['squad']].append(pr_info)
            pr_data['by_status'][pr_info['status']].append(pr_info)

            if pr_info['is_stale']:
                pr_data['stale_prs'].append(pr_info)
            if pr_info['is_draft']:
                pr_data['draft_prs'].append(pr_info)
                pr_data['summary']['draft'] += 1

            pr_data['summary']['total_open'] += 1
            pr_data['summary'][pr_info['status']] += 1
            if pr_info['is_stale']:
                pr_data['summary']['stale'] += 1

    total_age = sum(
        pr['age_days']
        for prs in pr_data['by_squad'].values()
        for pr in prs
    )
    if pr_data['summary']['total_open'] > 0:
        pr_data['summary']['avg_age_days'] = round(
            total_age / pr_data['summary']['total_open'], 1
        )

    # Convert sets to sorted lists
    pr_data['all_labels'] = sorted(list(pr_data['all_labels']))
    pr_data['all_branches'] = sorted(list(pr_data['all_branches']))

    print(f"\n✅ Processed {processed_count[0]} PRs")
    print(f"   Total open: {pr_data['summary']['total_open']}")
    print(f"   Waiting reviewer: {pr_data['summary']['waiting_reviewer']}")
    print(f"   Waiting author: {pr_data['summary']['waiting_author']}")
    print(f"   Approved: {pr_data['summary']['approved']}")
    print(f"   Stale: {pr_data['summary']['stale']}")
    print(f"   Draft: {pr_data['summary']['draft']}")

    return pr_data


def generate_html_dashboard(pr_data):
    """Generate HTML dashboard from PR data"""
    
    def tojson_filter(value):
        return Markup(json.dumps(value))
    env = Environment(autoescape=True)
    env.filters['tojson'] = tojson_filter
    template = env.from_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'unsafe-inline'; connect-src https://api.github.com; img-src data:;">
    <title>OCS-CI PR Pulse</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .header .subtitle {
            opacity: 0.9;
            font-size: 1.1em;
        }
        
        .header .last-updated {
            margin-top: 15px;
            opacity: 0.8;
            font-size: 0.9em;
        }
        
        .summary-cards {
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
        }
        
        .card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
            transition: all 0.2s;
            cursor: pointer;
            position: relative;
        }
        
        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        .card.active {
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.3);
            transform: scale(1.05);
        }
        
        .card::after {
            content: 'Click to filter';
            position: absolute;
            bottom: 5px;
            left: 0;
            right: 0;
            font-size: 0.7em;
            color: #9ca3af;
            opacity: 0;
            transition: opacity 0.2s;
        }
        
        .card:hover::after {
            opacity: 1;
        }
        
        .card-value {
            font-size: 2.5em;
            font-weight: bold;
            margin: 10px 0;
        }
        
        .card-label {
            color: #4b5563;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .card.total { border-top: 4px solid #667eea; }
        .card.total .card-value { color: #667eea; }
        
        .card.reviewer { border-top: 4px solid #f59e0b; }
        .card.reviewer .card-value { color: #f59e0b; }
        
        .card.author { border-top: 4px solid #3b82f6; }
        .card.author .card-value { color: #3b82f6; }
        
        .card.approved { border-top: 4px solid #10b981; }
        .card.approved .card-value { color: #10b981; }
        
        .card.stale { border-top: 4px solid #ef4444; }
        .card.stale .card-value { color: #ef4444; }
        
        .card.draft { border-top: 4px solid #9ca3af; }
        .card.draft .card-value { color: #9ca3af; }
        

        .card:focus-visible {
            outline: 2px solid #667eea;
            outline-offset: 2px;
        }

        .pr-table th.sorted-asc::after { content: ' ▲'; }
        .pr-table th.sorted-desc::after { content: ' ▼'; }

        .filter-count {
            display: inline-block;
            background: white;
            color: #667eea;
            font-size: 0.75em;
            font-weight: 700;
            width: 18px;
            height: 18px;
            line-height: 18px;
            border-radius: 50%;
            text-align: center;
            margin-left: 6px;
        }
        
        .tabs {
            display: flex;
            background: #f8f9fa;
            padding: 0 30px;
            border-bottom: 2px solid #e5e7eb;
        }
        
        .tab {
            padding: 15px 25px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 1em;
            font-weight: 500;
            color: #666;
            transition: all 0.3s;
            border-bottom: 3px solid transparent;
        }
        
        .tab:hover {
            color: #667eea;
        }
        
        .tab.active {
            color: #667eea;
            border-bottom-color: #667eea;
        }
        
        .tab-content {
            display: none;
            padding: 30px;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .squad-section {
            margin-bottom: 30px;
        }
        
        .squad-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            border-radius: 8px 8px 0 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .squad-name {
            font-size: 1.3em;
            font-weight: bold;
            text-transform: capitalize;
        }
        
        .squad-count {
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 0.9em;
        }
        
        .pr-table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-radius: 0 0 8px 8px;
            overflow: hidden;
        }
        
        .pr-table th {
            background: #f8f9fa;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
        }
        
        .pr-table td {
            padding: 12px;
            border-bottom: 1px solid #f3f4f6;
        }
        
        .pr-table tr:hover {
            background: #f9fafb;
        }
        
        .pr-link {
            color: #667eea;
            text-decoration: none;
            font-weight: 500;
        }
        
        .pr-link:hover {
            text-decoration: underline;
        }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 500;
            cursor: help;
        }
        
        .badge[title] {
            position: relative;
        }
        
        .badge.waiting-reviewer {
            background: #fef3c7;
            color: #92400e;
        }
        
        .badge.waiting-author {
            background: #dbeafe;
            color: #1e40af;
        }
        
        .badge.approved {
            background: #d1fae5;
            color: #065f46;
        }
        
        .badge.stale {
            background: #fee2e2;
            color: #991b1b;
        }
        
        .badge.draft {
            background: #f3f4f6;
            color: #6b7280;
            border: 1px dashed #9ca3af;
        }
        
        .badge.size-xs { background: #e0e7ff; color: #3730a3; }
        .badge.size-s { background: #dbeafe; color: #1e40af; }
        .badge.size-m { background: #fef3c7; color: #92400e; }
        .badge.size-l { background: #fed7aa; color: #9a3412; }
        .badge.size-xl { background: #fee2e2; color: #991b1b; }

        .badge.verified { background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7; }
        .badge.release-branch { background: #ede9fe; color: #5b21b6; font-size: 0.75em; }
        .badge.infrastructure { background: #f3f4f6; color: #4b5563; font-size: 0.75em; }

        .suggested-reviewer {
            display: inline-block;
            background: #eff6ff;
            color: #1e40af;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.8em;
            margin: 1px;
            border: 1px solid #bfdbfe;
        }
        .suggested-reviewer .score {
            font-size: 0.75em;
            color: #6b7280;
            margin-left: 2px;
        }
        .load-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 4px;
        }
        .load-light { background: #10b981; }
        .load-moderate { background: #3b82f6; }
        .load-heavy { background: #f59e0b; }
        .load-overloaded { background: #ef4444; }
        
        .age-indicator {
            font-weight: 500;
        }
        
        .age-fresh { color: #059669; }
        .age-normal { color: #b45309; }
        .age-old { color: #dc2626; }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #9ca3af;
        }
        
        .empty-state svg {
            width: 80px;
            height: 80px;
            margin-bottom: 20px;
            opacity: 0.5;
        }
        
        .filters {
            padding: 20px 30px;
            background: #f8f9fa;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
        }
        
        .filter-group {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .filter-group label {
            font-weight: 500;
            color: #374151;
        }
        
        .filter-group select,
        .filter-group input {
            padding: 10px 12px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            font-size: 0.9em;
            min-height: 44px;
        }
        
        /* Autocomplete styles */
        .autocomplete-wrapper {
            position: relative;
        }
        
        .autocomplete-suggestions {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: white;
            border: 1px solid #d1d5db;
            border-top: none;
            border-radius: 0 0 6px 6px;
            max-height: 200px;
            overflow-y: auto;
            display: none;
            z-index: 1000;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        
        .autocomplete-suggestions.show {
            display: block;
        }
        
        .autocomplete-item {
            padding: 12px 14px;
            cursor: pointer;
            border-bottom: 1px solid #f3f4f6;
            min-height: 44px;
            display: flex;
            align-items: center;
        }

        .autocomplete-item:hover, .autocomplete-item:focus {
            background: #eff6ff;
        }
        
        .autocomplete-item:last-child {
            border-bottom: none;
        }
        
        @media (max-width: 768px) {
            .summary-cards {
                grid-template-columns: repeat(2, 1fr);
            }
            
            .pr-table {
                font-size: 0.9em;
            }
            
            .pr-table th,
            .pr-table td {
                padding: 8px;
            }
        }
        button:focus-visible {
            outline: 2px solid #667eea;
            outline-offset: 2px;
        }

        select:focus-visible, input:focus-visible {
            outline: 2px solid #667eea;
            outline-offset: 1px;
        }

        .skip-link {
            position: absolute;
            top: -100px;
            left: 16px;
            background: #1f2937;
            color: white;
            padding: 10px 18px;
            border-radius: 0 0 6px 6px;
            z-index: 10001;
            font-weight: 600;
            text-decoration: none;
        }

        .skip-link:focus {
            top: 0;
        }

        .analytics-section {
            margin: 20px 0 !important;
        }

        @keyframes slideIn {
            from {
                transform: translateX(400px);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        
        @keyframes slideOut {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(400px);
                opacity: 0;
            }
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js" integrity="sha384-e6nUZLBkQ86NJ6TVVKAeSaK8jWa3NhkYWZFomE39AvDbQWeie9PlQqM3pmYW5d1g" crossorigin="anonymous" defer></script>
</head>
<body>
    <a href="#prTable" class="skip-link">Skip to PR Table</a>
    <div class="container">
        <div class="header">
            <h1>🔍 OCS-CI PR Pulse</h1>
            <div class="subtitle">Real-time Pull Request Status Tracking</div>
            <div class="last-updated">Last Updated: {{ generated_at }}</div>
        </div>
        
        <div class="summary-cards">
            <div class="card total" role="button" tabindex="0" onclick="filterByCard('all')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('all')}" aria-label="Filter: show all {{ summary.total_open }} open PRs" title="Total number of open pull requests">
                <div class="card-label">Total Open PRs</div>
                <div class="card-value">{{ summary.total_open }}</div>
            </div>
            <div class="card reviewer" role="button" tabindex="0" onclick="filterByCard('waiting_reviewer')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('waiting_reviewer')}" aria-label="Filter: show {{ summary.waiting_reviewer }} PRs needing review" title="Needs Review - PR is ready for initial review OR author has addressed feedback. Action: Reviewers should review">
                <div class="card-label">Needs Review</div>
                <div class="card-value">{{ summary.waiting_reviewer }}</div>
            </div>
            <div class="card author" role="button" tabindex="0" onclick="filterByCard('waiting_author')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('waiting_author')}" aria-label="Filter: show {{ summary.waiting_author }} PRs needing changes" title="Needs Changes - Reviewer requested changes OR left review comments. Action: Author should address feedback">
                <div class="card-label">Needs Changes</div>
                <div class="card-value">{{ summary.waiting_author }}</div>
            </div>
            <div class="card approved" role="button" tabindex="0" onclick="filterByCard('approved')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('approved')}" aria-label="Filter: show {{ summary.approved }} approved PRs" title="Approved - PR has been approved by one or more reviewers. Ready to merge">
                <div class="card-label">Approved</div>
                <div class="card-value">{{ summary.approved }}</div>
            </div>
            <div class="card stale" role="button" tabindex="0" onclick="filterByCard('stale')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('stale')}" aria-label="Filter: show {{ summary.stale }} stale PRs" title="PRs with no activity for more than 7 days">
                <div class="card-label">Stale (>7 days)</div>
                <div class="card-value">{{ summary.stale }}</div>
            </div>
            <div class="card draft" role="button" tabindex="0" onclick="filterByCard('draft')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();filterByCard('draft')}" aria-label="Filter: show {{ summary.draft }} draft PRs" title="Draft or work-in-progress pull requests">
                <div class="card-label">Draft / WIP</div>
                <div class="card-value">{{ summary.draft }}</div>
            </div>
        </div>
        <!-- Analytics Section -->
        <div class="analytics-section" style="background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden;">
            <div class="section-header" role="button" tabindex="0" onclick="toggleAnalytics()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleAnalytics()}" style="padding: 16px 20px; background: #1f2937; color: white; cursor: pointer; display: flex; justify-content: space-between; align-items: center;">
                <h2 style="margin: 0; font-size: 1.2em; font-weight: 600;">📈 Trends & Analytics</h2>
                <button class="toggle-btn" id="analyticsToggle" style="background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: white; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85em;">▼ Show Charts</button>
            </div>
            
            <div id="analyticsContent" class="analytics-content" style="display: none; padding: 30px;">
                <div class="charts-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 30px;">
                    
                    <!-- PR Volume Trend -->
                    <div class="chart-container" style="background: #f8f9fa; padding: 25px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <h3 style="margin: 0 0 20px 0; color: #1f2937; font-size: 1.1em;">📊 Total Open PRs Over Time (Last 4 Weeks)</h3>
                        <canvas id="volumeChart" style="max-height: 300px;"></canvas>
                    </div>
                    
                    <!-- Status Distribution -->
                    <div class="chart-container" style="background: #f8f9fa; padding: 25px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <h3 style="margin: 0 0 20px 0; color: #1f2937; font-size: 1.1em;">🎯 Status Distribution (with %)</h3>
                        <canvas id="statusChart" style="max-height: 300px;"></canvas>
                    </div>
                    
                    <!-- Age Distribution -->
                    <div class="chart-container" style="background: #f8f9fa; padding: 25px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                        <h3 style="margin: 0 0 20px 0; color: #1f2937; font-size: 1.1em;">⏰ Age Distribution</h3>
                        <canvas id="ageChart" style="max-height: 300px;"></canvas>
                    </div>
                    
                </div>
            </div>
        </div>
        
        
        <!-- Reviewer Workload Section -->
        <div class="analytics-section" style="background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden;">
            <div class="section-header" role="button" tabindex="0" onclick="toggleWorkload()" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleWorkload()}" style="padding: 16px 20px; background: #374151; color: white; cursor: pointer; display: flex; justify-content: space-between; align-items: center;">
                <h2 style="margin: 0; font-size: 1.2em; font-weight: 600;">👥 Team Review Workload</h2>
                <button class="toggle-btn" id="workloadToggle" style="background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: white; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85em;">▼ Show</button>
            </div>

            <div id="workloadContent" style="display: none; padding: 30px;">
                <!-- Getting Started hint (hidden once team is configured) -->
                <div id="onboardingHint" style="display: none; background: #fefce8; padding: 16px 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #fde68a; line-height: 1.6;">
                    <span style="color: #713f12;">Enter your team's GitHub usernames below. Filter by squad/label. Click <em>Analyze &amp; Assign PRs</em> to see review distribution and get smart assignment suggestions.</span>
                </div>
                <!-- Team Configuration -->
                <div style="background: #eff6ff; padding: 20px; border-radius: 8px; margin-bottom: 25px; border: 1px solid #bfdbfe;">
                    <div style="font-weight: 700; color: #1e40af; margin-bottom: 12px; font-size: 1.1em;">Configure Your Team</div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 12px;">
                        <div class="autocomplete-wrapper">
                            <label style="display: block; font-weight: 500; color: #374151; margin-bottom: 4px;">Team Members (GitHub usernames, comma-separated):</label>
                            <input type="text" id="teamMembersInput" placeholder="e.g. user1, user2, user3" autocomplete="off" oninput="showUsernameSuggestions(); autoSaveTeamSettings()" style="width: 100%; padding: 10px 14px; border: 2px solid #bfdbfe; border-radius: 6px; font-size: 1em; min-height: 44px;">
                            <div id="usernameSuggestions" class="autocomplete-suggestions"></div>
                        </div>
                        <div class="autocomplete-wrapper">
                            <label style="display: block; font-weight: 500; color: #374151; margin-bottom: 4px;">Filter PRs by (comma-separated squads or labels):</label>
                            <input type="text" id="teamFilterInput" placeholder="e.g. magenta, green OR team/e2e, team/ui" autocomplete="off" oninput="showFilterSuggestions(); autoSaveTeamSettings()" style="width: 100%; padding: 10px 14px; border: 2px solid #bfdbfe; border-radius: 6px; font-size: 1em; min-height: 44px;">
                            <div id="filterSuggestions" class="autocomplete-suggestions"></div>
                        </div>
                    </div>
                    <div style="display: flex; gap: 10px; align-items: center;">
                        <button onclick="analyzeTeam()" style="padding: 10px 24px; background: #2563eb; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95em; font-weight: 600; min-height: 44px;">Analyze & Assign PRs</button>
                        <button onclick="confirmClearSettings()" style="padding: 10px 16px; background: white; color: #6b7280; border: 1px solid #d1d5db; border-radius: 6px; cursor: pointer; font-size: 0.9em; min-height: 44px;">Clear All</button>
                    </div>
                </div>

                <!-- Dynamic Summary Cards -->
                <div id="workloadSummaryCards" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 15px; margin-bottom: 25px;"></div>

                <div id="workloadDataSection" style="display: none;">
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 30px; margin-bottom: 30px;">
                    <!-- Workload Distribution Chart -->
                    <div style="background: #f8f9fa; padding: 25px; border-radius: 12px;">
                        <h3 style="margin: 0 0 20px 0; color: #1f2937; font-size: 1.1em;">📊 Review Load by Member</h3>
                        <canvas id="workloadChart" style="max-height: 350px;"></canvas>
                    </div>

                    <!-- Team Workload Table -->
                    <div style="background: #f8f9fa; padding: 25px; border-radius: 12px;">
                        <h3 style="margin: 0 0 20px 0; color: #1f2937; font-size: 1.1em;">📋 Team Workload</h3>
                        <div id="workloadTableContainer"></div>
                    </div>
                </div>

                <!-- PR Assignment List -->
                <div id="assignmentSection" style="display: none;">
                    <div style="background: #f0fdf4; padding: 20px; border-radius: 8px; border: 1px solid #bbf7d0; margin-bottom: 20px;">
                        <h3 style="margin: 0 0 5px 0; color: #166534; font-size: 1.1em;">📝 Suggested PR Assignments</h3>
                        <div style="color: #4b5563; font-size: 0.85em;">PRs needing review are distributed evenly. Uncheck to skip, use the dropdown to reassign. Click "Assign on GitHub" to apply.</div>
                    </div>
                    <!-- GitHub Token -->
                    <div style="background: #fefce8; padding: 15px; border-radius: 8px; border: 1px solid #fde68a; margin-bottom: 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <label style="font-weight: 500; color: #854d0e; white-space: nowrap;">GitHub Token:</label>
                        <input type="password" id="ghTokenInput" placeholder="ghp_..." style="flex: 1; min-width: 250px; padding: 8px 12px; border: 1px solid #fde68a; border-radius: 6px; font-size: 0.9em; font-family: monospace; min-height: 40px;">
                        <button onclick="saveGHToken()" style="padding: 8px 16px; background: #ca8a04; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em;">Save</button>
                        <span id="tokenStatus" style="font-size: 0.8em; color: #6b7280;"></span>
                        <div style="width: 100%; font-size: 0.8em; color: #92400e;">Token needs <code>repo</code> scope. Stored only in your browser's localStorage.</div>
                    </div>
                    <div id="assignmentList"></div>
                </div>
                </div>
            </div>
        </div>

        <!-- Filters -->
        <div class="filters">
            <div class="filter-group">
                <label>🔍 Search:</label>
                <input type="text" id="searchInput" placeholder="Search PRs, authors..." onkeyup="filterPRs()">
            </div>
            <div class="filter-group">
                <label>Squad:</label>
                <select id="squadFilter" onchange="filterPRs()">
                    <option value="">All Squads</option>
                    {% for squad in by_squad.keys()|sort %}
                    <option value="{{ squad }}">{{ squad|title }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <label>Status:</label>
                <select id="statusFilter" onchange="filterPRs()">
                    <option value="">All Status</option>
                    <option value="waiting_reviewer">Needs Review</option>
                    <option value="waiting_author">Needs Changes</option>
                    <option value="approved">Approved</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Size:</label>
                <select id="sizeFilter" onchange="filterPRs()">
                    <option value="">All Sizes</option>
                    <option value="XS">XS</option>
                    <option value="S">S</option>
                    <option value="M">M</option>
                    <option value="L">L</option>
                    <option value="XL">XL</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Branch:</label>
                <select id="branchFilter" onchange="filterPRs()">
                    <option value="">All Branches</option>
                    {% for branch in all_branches %}
                    <option value="{{ branch }}">{{ branch }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="filter-group">
                <label>PR Verification:</label>
                <select id="verifiedFilter" onchange="filterPRs()">
                    <option value="">All PRs</option>
                    <option value="true">Verified</option>
                    <option value="false">Unverified</option>
                </select>
            </div>
            <div class="filter-group autocomplete-wrapper">
                <label>Label:</label>
                <input type="text" id="labelFilter" placeholder="Type to search labels..." autocomplete="off" oninput="filterLabels()">
                <div id="labelSuggestions" class="autocomplete-suggestions"></div>
            </div>
            <div class="filter-group">
                <button id="clearFiltersBtn" onclick="resetFilters()" style="padding: 10px 16px; background: #667eea; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em; min-height: 44px;">
                    Clear Filters
                </button>
            </div>
        </div>
        
        <!-- Single Unified PR Table -->
        <div style="padding: 30px;">
            <div class="squad-section">
                <div class="squad-header">
                    <span class="squad-name" id="tableTitle">All Open Pull Requests</span>
                    <span class="squad-count" id="visibleCount">{{ summary.total_open }} PRs</span>
                </div>
                <table class="pr-table" id="prTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable(0)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(0)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">PR #</th>
                            <th onclick="sortTable(1)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(1)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Title</th>
                            <th onclick="sortTable(2)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(2)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Author</th>
                            <th onclick="sortTable(3)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(3)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Squad</th>
                            <th onclick="sortTable(4)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(4)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Branch</th>
                            <th onclick="sortTable(5)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(5)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Status</th>
                            <th onclick="sortTable(6)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(6)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Age</th>
                            <th onclick="sortTable(7)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();sortTable(7)}" tabindex="0" style="cursor: pointer;" aria-sort="none" role="columnheader">Size</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for squad, prs in by_squad.items()|sort %}
                            {% for pr in prs|sort(attribute='age_days', reverse=true) %}
                            <tr data-status="{{ pr.status }}" data-squad="{{ pr.squad }}" data-size="{{ pr.size }}" data-stale="{{ pr.is_stale|lower }}" data-draft="{{ pr.is_draft|lower }}" data-author="{{ pr.author }}" data-labels="{{ pr.labels|join(',') }}" data-branch="{{ pr.base_branch }}" data-verified="{{ pr.is_verified|lower }}" data-reviewers="{{ pr.reviewers|join(',') }}" data-actual-reviewers="{{ pr.actual_reviewers|join(',') }}">
                                <td><a href="{{ pr.url }}" class="pr-link" target="_blank">#{{ pr.number }}</a></td>
                                <td>
                                    {{ pr.title }}
                                    {% if pr.is_draft %}<span class="badge draft">DRAFT</span>{% endif %}
                                    {% if pr.is_stale %}<span class="badge stale">STALE</span>{% endif %}
                                    {% if pr.is_verified %}<span class="badge verified">VERIFIED</span>{% endif %}
                                </td>
                                <td>{{ pr.author }}</td>
                                <td>{{ pr.squad }}</td>
                                <td>{% if pr.base_branch != 'master' %}<span class="badge release-branch">{{ pr.base_branch }}</span>{% else %}master{% endif %}</td>
                                <td>
                                    <span class="badge {{ pr.status|replace('_', '-') }}" title="{% if pr.status == 'waiting_reviewer' %}Needs Review 🟡 - PR is ready for initial review OR author has addressed feedback (pushed new commits). Action: Reviewers should review{% elif pr.status == 'waiting_author' %}Needs Changes 🔴 - Reviewer requested changes OR left review comments (inline or body). Author hasn't pushed commits since review. Action: Author should address feedback{% elif pr.status == 'approved' %}Approved 🟢 - PR has been approved by one or more reviewers. Ready to merge. Action: Merge when ready{% endif %}">
                                        {% if pr.status == 'waiting_reviewer' %}NEEDS REVIEW{% elif pr.status == 'waiting_author' %}NEEDS CHANGES{% elif pr.status == 'approved' %}APPROVED{% else %}{{ pr.status|replace('_', ' ')|upper }}{% endif %}
                                    </span>
                                </td>
                                <td>
                                    <span class="age-indicator {% if pr.age_days < 3 %}age-fresh{% elif pr.age_days < 7 %}age-normal{% else %}age-old{% endif %}">
                                        {{ pr.age_days }}d
                                    </span>
                                </td>
                                <td><span class="badge size-{{ pr.size|lower }}">{{ pr.size }}</span></td>
                            </tr>
                            {% endfor %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    
    <script>
        function esc(str) {
            var d = document.createElement('div');
            d.appendChild(document.createTextNode(str));
            return d.innerHTML;
        }

        var storage = {
            get: function(key) { try { return storage.get(key); } catch(e) { return null; } },
            set: function(key, val) { try { storage.set(key, val); } catch(e) {} },
            remove: function(key) { try { storage.remove(key); } catch(e) {} }
        };
        var sessionStore = {
            get: function(key) { try { return sessionStore.get(key); } catch(e) { return null; } },
            set: function(key, val) { try { sessionStore.set(key, val); } catch(e) {} },
            remove: function(key) { try { sessionStore.remove(key); } catch(e) {} }
        };

        const FILTER_IDS = ['searchInput', 'squadFilter', 'statusFilter', 'sizeFilter', 'branchFilter', 'verifiedFilter', 'labelFilter'];

        function getActiveFilterCount() {
            let count = 0;
            FILTER_IDS.forEach(id => {
                const el = document.getElementById(id);
                if (el && el.value && el.value.trim() !== '') count++;
            });
            return count;
        }

        function updateClearButton() {
            const btn = document.getElementById('clearFiltersBtn');
            const count = getActiveFilterCount();
            if (count > 0) {
                btn.innerHTML = 'Clear Filters <span class="filter-count">' + count + '</span>';
            } else {
                btn.textContent = 'Clear Filters';
            }
        }

        function saveFiltersToSession() {
            const state = {};
            FILTER_IDS.forEach(id => {
                const el = document.getElementById(id);
                if (el) state[id] = el.value;
            });
            sessionStore.set('prDashboardFilters', JSON.stringify(state));
        }

        function restoreFiltersFromSession() {
            const saved = sessionStore.get('prDashboardFilters');
            if (!saved) return false;
            const state = JSON.parse(saved);
            let hasFilters = false;
            FILTER_IDS.forEach(id => {
                const el = document.getElementById(id);
                if (el && state[id]) {
                    el.value = state[id];
                    if (state[id].trim() !== '') hasFilters = true;
                }
            });
            return hasFilters;
        }

        function filterPRs() {
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const squadFilter = document.getElementById('squadFilter').value.toLowerCase();
            const statusFilter = document.getElementById('statusFilter').value;
            const sizeFilter = document.getElementById('sizeFilter').value;
            const branchFilter = document.getElementById('branchFilter').value;
            const verifiedFilter = document.getElementById('verifiedFilter').value;
            const labelFilter = document.getElementById('labelFilter').value.toLowerCase().trim();

            let visibleCount = 0;
            const rows = document.querySelectorAll('#prTable tbody tr');

            rows.forEach(row => {
                const prNumber = row.cells[0].textContent.toLowerCase().replace('#', '');
                const prTitle = row.cells[1].textContent.toLowerCase();
                const prAuthor = row.cells[2].textContent.toLowerCase();
                const prSquad = row.dataset.squad.toLowerCase();
                const prStatus = row.dataset.status;
                const prSize = row.dataset.size;
                const prBranch = row.dataset.branch;
                const prVerified = row.dataset.verified;
                const prLabels = row.dataset.labels ? row.dataset.labels.toLowerCase() : '';

                let show = true;

                if (searchTerm) {
                    const cleanSearchTerm = searchTerm.replace('#', '');
                    if (!prNumber.includes(cleanSearchTerm) &&
                        !prTitle.includes(searchTerm) &&
                        !prAuthor.includes(searchTerm)) {
                        show = false;
                    }
                }
                if (squadFilter && prSquad !== squadFilter) show = false;
                if (statusFilter && prStatus !== statusFilter) show = false;
                if (sizeFilter && prSize !== sizeFilter) show = false;
                if (branchFilter && prBranch !== branchFilter) show = false;
                if (verifiedFilter && prVerified !== verifiedFilter) show = false;
                if (labelFilter && !prLabels.includes(labelFilter)) show = false;

                row.style.display = show ? '' : 'none';
                if (show) visibleCount++;
            });

            document.getElementById('visibleCount').textContent = visibleCount + ' PRs';
            updateClearButton();
            saveFiltersToSession();
        }
        
        // Filter by clicking summary cards
        function filterByCard(filterType) {
            // Remove active class from all cards
            document.querySelectorAll('.card').forEach(card => {
                card.classList.remove('active');
            });
            
            // Reset all filters first
            document.getElementById('searchInput').value = '';
            document.getElementById('squadFilter').value = '';
            document.getElementById('statusFilter').value = '';
            document.getElementById('sizeFilter').value = '';
            document.getElementById('branchFilter').value = '';
            document.getElementById('verifiedFilter').value = '';
            
            // Apply filter based on card clicked
            if (filterType === 'all') {
                document.querySelector('.card.total').classList.add('active');
                document.getElementById('tableTitle').textContent = 'All Open Pull Requests';
            } else if (filterType === 'stale') {
                document.querySelector('.card.stale').classList.add('active');
                document.getElementById('tableTitle').textContent = 'Stale PRs (>7 days)';
                // Show only stale PRs
                const rows = document.querySelectorAll('#prTable tbody tr');
                let visibleCount = 0;
                rows.forEach(row => {
                    const isStale = row.dataset.stale === 'true';
                    row.style.display = isStale ? '' : 'none';
                    if (isStale) visibleCount++;
                });
                document.getElementById('visibleCount').textContent = visibleCount + ' PRs';
                return;
            } else if (filterType === 'draft') {
                document.querySelector('.card.draft').classList.add('active');
                document.getElementById('tableTitle').textContent = 'Draft / WIP PRs';
                // Show only draft PRs
                const rows = document.querySelectorAll('#prTable tbody tr');
                let visibleCount = 0;
                rows.forEach(row => {
                    const isDraft = row.dataset.draft === 'true';
                    row.style.display = isDraft ? '' : 'none';
                    if (isDraft) visibleCount++;
                });
                document.getElementById('visibleCount').textContent = visibleCount + ' PRs';
                return;
            } else {
                // Status filters
                const cardMap = {
                    'waiting_reviewer': { card: '.card.reviewer', title: 'Needs Review' },
                    'waiting_author': { card: '.card.author', title: 'Needs Changes' },
                    'approved': { card: '.card.approved', title: 'Approved PRs' }
                };
                
                if (cardMap[filterType]) {
                    document.querySelector(cardMap[filterType].card).classList.add('active');
                    document.getElementById('tableTitle').textContent = cardMap[filterType].title;
                    document.getElementById('statusFilter').value = filterType;
                }
            }
            
            filterPRs();
        }
        
        // Reset all filters
        function resetFilters() {
            FILTER_IDS.forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = '';
            });
            document.getElementById('labelSuggestions').classList.remove('show');

            document.querySelectorAll('.card').forEach(card => {
                card.classList.remove('active');
            });
            document.querySelector('.card.total').classList.add('active');

            document.getElementById('tableTitle').textContent = 'All Open Pull Requests';
            sessionStore.remove('prDashboardFilters');
            filterPRs();
        }
        
        // Label autocomplete functionality
        const allLabels = {{ all_labels|tojson }};
        
        function filterLabels() {
            const input = document.getElementById('labelFilter');
            const suggestions = document.getElementById('labelSuggestions');
            const searchTerm = input.value.toLowerCase().trim();
            
            if (!searchTerm) {
                suggestions.classList.remove('show');
                suggestions.innerHTML = '';
                filterPRs();
                return;
            }
            
            const matches = allLabels.filter(label =>
                label.toLowerCase().includes(searchTerm)
            );
            
            if (matches.length === 0) {
                suggestions.classList.remove('show');
                suggestions.innerHTML = '';
                filterPRs();
                return;
            }
            
            suggestions.innerHTML = matches.map(label =>
                `<div class="autocomplete-item" onclick="selectLabel('${esc(label)}')">${esc(label)}</div>`
            ).join('');
            suggestions.classList.add('show');
            
            // Filter PRs as user types
            filterPRs();
        }
        
        function selectLabel(label) {
            document.getElementById('labelFilter').value = label;
            document.getElementById('labelSuggestions').classList.remove('show');
            filterPRs();
        }
        
        // Close suggestions when clicking outside
        document.addEventListener('click', function(e) {
            const labelFilter = document.getElementById('labelFilter');
            const suggestions = document.getElementById('labelSuggestions');
            if (e.target !== labelFilter && !suggestions.contains(e.target)) {
                suggestions.classList.remove('show');
            }
        });
        
        // Sort table by column
        let sortDirection = {};
        let currentSortColumn = null;
        function sortTable(columnIndex) {
            const table = document.getElementById('prTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const headers = table.querySelectorAll('th');

            // Toggle sort direction
            sortDirection[columnIndex] = !sortDirection[columnIndex];
            const ascending = sortDirection[columnIndex];

            // Update header classes and aria-sort
            headers.forEach((th, i) => {
                th.classList.remove('sorted-asc', 'sorted-desc');
                th.setAttribute('aria-sort', 'none');
            });
            headers[columnIndex].classList.add(ascending ? 'sorted-asc' : 'sorted-desc');
            headers[columnIndex].setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
            currentSortColumn = columnIndex;

            rows.sort((a, b) => {
                let aVal = a.cells[columnIndex].textContent.trim();
                let bVal = b.cells[columnIndex].textContent.trim();

                if (columnIndex === 0) {
                    aVal = parseInt(aVal.replace('#', ''));
                    bVal = parseInt(bVal.replace('#', ''));
                } else if (columnIndex === 6) {
                    aVal = parseInt(aVal.replace('d', ''));
                    bVal = parseInt(bVal.replace('d', ''));
                }

                if (aVal < bVal) return ascending ? -1 : 1;
                if (aVal > bVal) return ascending ? 1 : -1;
                return 0;
            });

            rows.forEach(row => tbody.appendChild(row));
        }
        
        // Initialize - restore filters and auto-expand sections with saved state
        window.onload = function() {
            if (restoreFiltersFromSession()) {
                filterPRs();
            } else {
                filterByCard('all');
            }
            // Auto-open workload section if it was previously open OR if team is configured
            const wasOpen = storage.get('prDashboardWorkloadOpen') === 'true';
            const hasTeam = storage.get('prDashboardTeam');
            if (wasOpen || hasTeam) {
                toggleWorkload();
            }
        };
        
        // Filter PRs by age range
        function filterByAgeRange(ageRange) {
            // Parse age range
            let minAge = 0;
            let maxAge = Infinity;
            
            if (ageRange === '0-2 days') {
                minAge = 0;
                maxAge = 2;
            } else if (ageRange === '3-7 days') {
                minAge = 3;
                maxAge = 7;
            } else if (ageRange === '8-14 days') {
                minAge = 8;
                maxAge = 14;
            } else if (ageRange === '15-30 days') {
                minAge = 15;
                maxAge = 30;
            } else if (ageRange === '30-60 days') {
                minAge = 30;
                maxAge = 60;
            } else if (ageRange === '60+ days') {
                minAge = 60;
                maxAge = Infinity;
            }
            
            // Filter table rows
            let visibleCount = 0;
            const rows = document.querySelectorAll('#prTable tbody tr');
            
            rows.forEach(row => {
                const ageText = row.cells[6].textContent.trim();
                const age = parseInt(ageText.replace('d', ''));
                
                if (age >= minAge && age <= maxAge) {
                    row.style.display = '';
                    visibleCount++;
                } else {
                    row.style.display = 'none';
                }
            });
            
            // Update visible count and title
            document.getElementById('visibleCount').textContent = visibleCount + ' PRs';
            document.getElementById('tableTitle').textContent = 'PRs aged ' + ageRange;
            
            // Scroll to table
            document.getElementById('prTable').scrollIntoView({ behavior: 'smooth', block: 'start' });
            
            // Show notification
            showNotification('Filtered to ' + visibleCount + ' PRs aged ' + ageRange);
        }
        
        // Show notification
        function showNotification(message) {
            const notification = document.createElement('div');
            notification.textContent = message;
            notification.style.cssText = 'position: fixed; top: 20px; right: 20px; background: #667eea; color: white; padding: 15px 25px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.2); z-index: 10000; animation: slideIn 0.3s ease-out;';
            document.body.appendChild(notification);
            
            setTimeout(() => {
                notification.style.animation = 'slideOut 0.3s ease-out';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
        
        // All reviewer profiles and PR data (built server-side)
        const allReviewerProfiles = {{ reviewer_profiles|tojson }};
        const defaultTeam = {{ default_team|tojson }};
        const allUsernames = Object.keys(allReviewerProfiles).sort();
        const allSquadNames = {{ by_squad.keys()|list|tojson }};
        let workloadChartInstance = null;

        // Get the current token being typed in a comma-separated input
        function getCurrentToken(input) {
            const val = input.value;
            const cursor = input.selectionStart;
            const before = val.substring(0, cursor);
            const lastComma = before.lastIndexOf(',');
            return before.substring(lastComma + 1).trim().toLowerCase();
        }

        // Replace the current token with the selected value
        function replaceCurrentToken(input, value) {
            const val = input.value;
            const cursor = input.selectionStart;
            const before = val.substring(0, cursor);
            const after = val.substring(cursor);
            const lastComma = before.lastIndexOf(',');
            const prefix = lastComma >= 0 ? before.substring(0, lastComma + 1) + ' ' : '';
            const afterComma = after.indexOf(',');
            const suffix = afterComma >= 0 ? after.substring(afterComma) : '';
            input.value = prefix + value + ', ' + suffix.replace(/^,\\s*/, '');
            input.focus();
        }

        function showUsernameSuggestions() {
            const input = document.getElementById('teamMembersInput');
            const box = document.getElementById('usernameSuggestions');
            const token = getCurrentToken(input);
            if (!token) { box.classList.remove('show'); box.innerHTML = ''; return; }

            const already = parseCommaSeparated(input.value);
            const matches = allUsernames.filter(u =>
                u.toLowerCase().includes(token) && !already.includes(u.toLowerCase())
            ).slice(0, 10);

            if (matches.length === 0) { box.classList.remove('show'); box.innerHTML = ''; return; }
            box.innerHTML = matches.map(u =>
                `<div class="autocomplete-item" onmousedown="selectUsername('${esc(u)}')">${esc(u)}</div>`
            ).join('');
            box.classList.add('show');
        }

        function selectUsername(username) {
            const input = document.getElementById('teamMembersInput');
            replaceCurrentToken(input, username);
            document.getElementById('usernameSuggestions').classList.remove('show');
        }

        function showFilterSuggestions() {
            const input = document.getElementById('teamFilterInput');
            const box = document.getElementById('filterSuggestions');
            const token = getCurrentToken(input);
            if (!token) { box.classList.remove('show'); box.innerHTML = ''; return; }

            const already = parseCommaSeparated(input.value);
            const squadMatches = allSquadNames.filter(s =>
                s.toLowerCase().includes(token) && !already.includes(s.toLowerCase())
            );
            const labelMatches = allLabels.filter(l =>
                l.toLowerCase().includes(token) && !already.includes(l.toLowerCase())
            ).slice(0, 8);

            const results = [];
            squadMatches.forEach(s => results.push({value: s, type: 'squad'}));
            labelMatches.forEach(l => results.push({value: l, type: 'label'}));

            if (results.length === 0) { box.classList.remove('show'); box.innerHTML = ''; return; }
            box.innerHTML = results.map(r =>
                `<div class="autocomplete-item" onmousedown="selectFilter('${esc(r.value)}')">` +
                `<span style="background:${r.type === 'squad' ? '#e0e7ff;color:#3730a3' : '#fef3c7;color:#92400e'};padding:1px 6px;border-radius:8px;font-size:0.75em;margin-right:6px;">${esc(r.type)}</span>${esc(r.value)}</div>`
            ).join('');
            box.classList.add('show');
        }

        function selectFilter(value) {
            const input = document.getElementById('teamFilterInput');
            replaceCurrentToken(input, value);
            document.getElementById('filterSuggestions').classList.remove('show');
        }

        // Close team suggestions when clicking outside
        document.addEventListener('click', function(e) {
            const targets = {
                'usernameSuggestions': 'teamMembersInput',
                'filterSuggestions': 'teamFilterInput'
            };
            Object.entries(targets).forEach(function(pair) {
                const box = document.getElementById(pair[0]);
                if (box && !box.contains(e.target) && e.target.id !== pair[1]) {
                    box.classList.remove('show');
                }
            });
        });

        // Close all dropdowns on Escape
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                ['usernameSuggestions', 'filterSuggestions', 'labelSuggestions'].forEach(function(id) {
                    var box = document.getElementById(id);
                    if (box) box.classList.remove('show');
                });
            }
        });

        // Case-insensitive profile lookup
        function findProfile(username) {
            if (allReviewerProfiles[username]) return allReviewerProfiles[username];
            const lower = username.toLowerCase();
            for (const key of Object.keys(allReviewerProfiles)) {
                if (key.toLowerCase() === lower) return allReviewerProfiles[key];
            }
            return null;
        }

        // Collect all PR data from table rows for client-side filtering
        function getAllPRsFromTable() {
            const rows = document.querySelectorAll('#prTable tbody tr');
            const prs = [];
            rows.forEach(row => {
                prs.push({
                    number: row.cells[0].textContent.trim().replace('#', ''),
                    title: row.cells[1].textContent.trim().split('DRAFT')[0].split('STALE')[0].split('VERIFIED')[0].trim(),
                    author: row.dataset.author,
                    squad: row.dataset.squad,
                    status: row.dataset.status,
                    labels: row.dataset.labels ? row.dataset.labels.toLowerCase().split(',').filter(Boolean) : [],
                    age: parseInt(row.cells[6].textContent.trim().replace('d', '')),
                    url: row.cells[0].querySelector('a').href,
                    reviewers: row.dataset.reviewers ? row.dataset.reviewers.split(',').filter(Boolean) : [],
                    actualReviewers: row.dataset.actualReviewers ? row.dataset.actualReviewers.split(',').filter(Boolean) : [],
                });
            });
            return prs;
        }

        function parseCommaSeparated(val) {
            return val.split(',').map(s => s.trim()).filter(s => s.length > 0);
        }

        // Squad colors for visual variety
        function getSquadColor(squad) {
            const colors = {
                magenta: '#ec4899',   // Pink
                red: '#ef4444',       // Red
                black: '#374151',     // Dark gray
                blue: '#3b82f6',      // Blue
                brown: '#92400e',     // Brown
                purple: '#a855f7',    // Purple
                green: '#10b981',     // Green
                yellow: '#eab308',    // Yellow
                general: '#6b7280',   // Gray
                'team/e2e': '#0891b2' // Cyan
            };
            return colors[squad.toLowerCase()] || '#6b7280';
        }

        // Age color coding (for highlighting old PRs)
        function getAgeStyle(age) {
            if (age > 300) return 'color:#dc2626;font-weight:bold;'; // Ancient (red + bold)
            if (age > 180) return 'color:#f59e0b;'; // Old (orange)
            return 'color:#6b7280;'; // Normal (gray)
        }

        function getLoadColor(load) {
            if (load === 0) return '#9ca3af';  // Gray - Idle
            if (load >= 8) return '#ef4444';   // Red - Overloaded
            if (load >= 5) return '#f59e0b';   // Orange - Heavy
            return '#10b981';                   // Green - OK
        }

        function getStatusLabel(load) {
            if (load === 0) return 'idle';
            if (load >= 8) return 'overloaded';
            if (load >= 5) return 'heavy';
            return 'ok';
        }

        function getStatusBadge(load) {
            if (load === 0) return '<span style="background:#f3f4f6;color:#6b7280;padding:2px 8px;border-radius:8px;font-size:0.8em;">⚪ IDLE</span>';
            if (load >= 8) return '<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:8px;font-size:0.8em;">🔴 OVERLOADED</span>';
            if (load >= 5) return '<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:8px;font-size:0.8em;">🟡 HEAVY</span>';
            return '<span style="background:#d1fae5;color:#065f46;padding:2px 8px;border-radius:8px;font-size:0.8em;">🟢 OK</span>';
        }

        function confirmClearSettings() {
            if (confirm('Clear all saved settings (team members, filters, and GitHub token)?')) {
                clearTeamSettings();
            }
        }

        function clearTeamSettings() {
            storage.remove('prDashboardTeam');
            storage.remove('prDashboardTeamFilter');
            storage.remove('prDashboardGHToken');
            document.getElementById('teamMembersInput').value = '';
            document.getElementById('teamFilterInput').value = '';
            document.getElementById('ghTokenInput').value = '';
            document.getElementById('workloadSummaryCards').innerHTML = '';
            document.getElementById('workloadTableContainer').innerHTML = '';
            document.getElementById('assignmentList').innerHTML = '';
            document.getElementById('assignmentSection').style.display = 'none';
            document.getElementById('workloadDataSection').style.display = 'none';
            document.getElementById('onboardingHint').style.display = 'block';
            if (workloadChartInstance) {
                workloadChartInstance.destroy();
                workloadChartInstance = null;
            }
            showNotification('All saved settings cleared');
        }

        function analyzeTeam() {
            // Show loading state
            showNotification('⏳ Analyzing team workload...', 30000);
            const analyzeBtn = document.querySelector('button[onclick="analyzeTeam()"]');
            if (analyzeBtn) {
                analyzeBtn.disabled = true;
                analyzeBtn.textContent = '⏳ Analyzing...';
            }

            const membersInput = document.getElementById('teamMembersInput').value.trim();
            const filterInput = document.getElementById('teamFilterInput').value.trim();
            const members = parseCommaSeparated(membersInput);

            if (members.length === 0) {
                if (analyzeBtn) {
                    analyzeBtn.disabled = false;
                    analyzeBtn.textContent = 'Analyze & Assign PRs';
                }
                showNotification('⚠️ Please enter at least one GitHub username');
                return;
            }

            storage.set('prDashboardTeam', membersInput);
            storage.set('prDashboardTeamFilter', filterInput);
            var hint = document.getElementById('onboardingHint');
            if (hint) hint.style.display = 'none';

            const filters = parseCommaSeparated(filterInput);
            const allPRs = getAllPRsFromTable();

            // Filter PRs that match the team's squads or labels
            let teamPRs;
            if (filters.length === 0) {
                teamPRs = allPRs;
            } else {
                teamPRs = allPRs.filter(pr => {
                    const squadMatch = filters.some(f => pr.squad.toLowerCase() === f);
                    const labelMatch = filters.some(f => pr.labels.some(l => l === f));
                    return squadMatch || labelMatch;
                });
            }

            const needsReview = teamPRs.filter(pr => pr.status === 'waiting_reviewer' && pr.reviewers.length === 0);

            document.getElementById('workloadDataSection').style.display = 'block';
            renderWorkloadView(members, teamPRs, needsReview);
            renderAssignments(members, needsReview);

            const filterDesc = filters.length > 0 ? ` matching "${filters.join(', ')}"` : '';
            showNotification(`✓ Analysis complete! Found ${teamPRs.length} PRs${filterDesc}. ${needsReview.length} need review.`);

            // Re-enable analyze button
            const analyzeBtnEnd = document.querySelector('button[onclick="analyzeTeam()"]');
            if (analyzeBtnEnd) {
                analyzeBtnEnd.disabled = false;
                analyzeBtnEnd.textContent = 'Analyze & Assign PRs';
            }
        }

        function renderWorkloadView(members, teamPRs, needsReview) {
            const profiles = members.map(m => {
                const p = findProfile(m);
                return p ? {...p} : {login: m, open_reviews: 0, total_reviews: 0, squad_expertise: {}, open_pr_numbers: []};
            });
            profiles.sort((a, b) => b.open_reviews - a.open_reviews);

            const idle = profiles.filter(p => p.open_reviews === 0);
            const active = profiles.filter(p => p.open_reviews > 0);
            const overloaded = profiles.filter(p => p.open_reviews >= 8);
            const loads = profiles.map(p => p.open_reviews);
            const avgLoad = loads.length > 0 ? (loads.reduce((a,b) => a+b, 0) / loads.length).toFixed(1) : '0';

            document.getElementById('workloadSummaryCards').innerHTML = `
                <div style="background:#f0fdf4;padding:15px;border-radius:8px;text-align:center;border:1px solid #bbf7d0;">
                    <div style="font-size:2em;font-weight:bold;color:#16a34a;">${members.length}</div>
                    <div style="color:#166534;font-size:0.85em;">Team Members</div>
                </div>
                <div style="background:#eff6ff;padding:15px;border-radius:8px;text-align:center;border:1px solid #bfdbfe;">
                    <div style="font-size:2em;font-weight:bold;color:#2563eb;">${teamPRs.length}</div>
                    <div style="color:#1e40af;font-size:0.85em;">Team's PRs</div>
                </div>
                <div style="background:#fef3c7;padding:15px;border-radius:8px;text-align:center;border:1px solid #fde68a;">
                    <div style="font-size:2em;font-weight:bold;color:#b45309;">${needsReview.length}</div>
                    <div style="color:#92400e;font-size:0.85em;">Need Review</div>
                </div>
                <div style="background:#f3f4f6;padding:15px;border-radius:8px;text-align:center;border:1px solid #d1d5db;">
                    <div style="font-size:2em;font-weight:bold;color:#6b7280;">${idle.length}</div>
                    <div style="color:#4b5563;font-size:0.85em;">Idle Members</div>
                    ${idle.length > 0 ? '<div style="margin-top:5px;font-size:0.8em;color:#6b7280;">' + idle.map(p=>esc(p.login)).join(', ') + '</div>' : ''}
                </div>
                <div style="background:#fefce8;padding:15px;border-radius:8px;text-align:center;border:1px solid #fde68a;">
                    <div style="font-size:2em;font-weight:bold;color:#ca8a04;">${avgLoad}</div>
                    <div style="color:#854d0e;font-size:0.85em;">Avg Load</div>
                </div>
                <div style="background:#fef2f2;padding:15px;border-radius:8px;text-align:center;border:1px solid #fecaca;">
                    <div style="font-size:2em;font-weight:bold;color:#dc2626;">${overloaded.length}</div>
                    <div style="color:#991b1b;font-size:0.85em;">Overloaded (8+)</div>
                </div>
            `;

            // Adaptive table height: show all for small/medium teams, scroll only for large teams
            const memberCount = profiles.length;
            const container = document.getElementById('workloadTableContainer');

            // Set container style based on team size
            // Note: Row heights vary based on number of PRs shown, so fixed heights don't work well
            if (memberCount <= 12) {
                // Small/medium teams: show all members, no scroll
                container.style.maxHeight = 'none';
                container.style.overflowY = 'visible';
                container.style.position = 'relative';
            } else {
                // Large teams (13+): limit height and scroll
                container.style.maxHeight = '700px';
                container.style.overflowY = 'auto';
                container.style.position = 'relative';
            }

            // Team table
            let tableHtml = '<table style="width:100%;border-collapse:collapse;font-size:0.9em;"><thead><tr style="background:#e5e7eb;">' +
                '<th style="padding:8px;text-align:left;">Member</th>' +
                '<th style="padding:8px;text-align:center;">Current Load</th>' +
                '<th style="padding:8px;text-align:center;">Total Reviews</th>' +
                '<th style="padding:8px;text-align:left;">Expertise</th>' +
                '<th style="padding:8px;text-align:center;">Status</th></tr></thead><tbody>';

            profiles.forEach((p, idx) => {
                const bgStyle = p.open_reviews === 0 ? 'background:#fef9ef;' : (p.open_reviews >= 8 ? 'background:#fef2f2;' : '');
                // Sort squad experience by count (highest first)
                const sortedExpertise = Object.entries(p.squad_expertise || {})
                    .sort((a, b) => b[1] - a[1]); // Sort descending by count

                const expertiseHtml = sortedExpertise.map(([sq, cnt]) =>
                    `<span style="background:#e0e7ff;color:#3730a3;padding:1px 6px;border-radius:8px;font-size:0.8em;margin:1px;">${esc(sq)}:${cnt}</span>`
                ).join(' ') || '<span style="color:#374151;font-size:0.85em;">🌱 Getting started</span>';

                const prNums = p.open_pr_numbers || [];
                let loadHtml;
                if (prNums.length > 0) {
                    const links = prNums.map(n => `<a href="https://github.com/red-hat-storage/ocs-ci/pull/${n}" target="_blank" style="color:#667eea;text-decoration:none;font-size:0.8em;">#${n}</a>`).join(', ');
                    loadHtml = `<td style="padding:8px;text-align:center;"><span style="font-weight:bold;font-size:1.2em;">${p.open_reviews}</span><div style="margin-top:4px;">${links}</div></td>`;
                } else {
                    loadHtml = `<td style="padding:8px;text-align:center;font-weight:bold;font-size:1.2em;">0</td>`;
                }

                tableHtml += `<tr style="border-bottom:1px solid #e5e7eb;${bgStyle}">
                    <td style="padding:8px;"><span role="img" aria-label="Load status: ${getStatusLabel(p.open_reviews)}" style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;background:${getLoadColor(p.open_reviews)};"></span><strong>${esc(p.login)}</strong></td>
                    ${loadHtml}
                    <td style="padding:8px;text-align:center;">${p.total_reviews}</td>
                    <td style="padding:8px;">${expertiseHtml}</td>
                    <td style="padding:8px;text-align:center;">${getStatusBadge(p.open_reviews)}</td></tr>`;
            });
            tableHtml += '</tbody></table>';

            // Add scroll indicator if team is very large
            if (memberCount > 12) {
                const visibleCount = 10; // Approximate rows visible in 700px
                const hiddenCount = memberCount - visibleCount;
                if (hiddenCount > 0) {
                    tableHtml += `<div style="position:sticky;bottom:0;left:0;right:0;background:linear-gradient(to top, #f8f9fa 60%, transparent);padding:8px;text-align:center;font-size:0.8em;color:#6b7280;pointer-events:none;">
                        ↓ Scroll for ${hiddenCount} more member${hiddenCount !== 1 ? 's' : ''} ↓
                    </div>`;
                }
            }

            container.innerHTML = tableHtml;

            // Chart
            if (typeof Chart === 'undefined') return;
            if (workloadChartInstance) workloadChartInstance.destroy();
            const ctx = document.getElementById('workloadChart').getContext('2d');
            workloadChartInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: profiles.map(p => p.login),
                    datasets: [{
                        label: 'Current Load',
                        data: profiles.map(p => p.open_reviews),
                        backgroundColor: profiles.map(p => getLoadColor(p.open_reviews)),
                        borderWidth: 0
                    }, {
                        label: 'Total Reviews',
                        data: profiles.map(p => p.total_reviews),
                        backgroundColor: 'rgba(102,126,234,0.2)',
                        borderColor: '#667eea',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: true, indexAxis: 'y',
                    plugins: { legend: { position: 'top' } },
                    scales: { x: { beginAtZero: true, ticks: { stepSize: 1 } } }
                }
            });
        }

        // Global assignment state — tracks reassignments
        let currentAssignments = {};
        let currentMembers = [];

        function renderAssignments(members, needsReview) {
            const section = document.getElementById('assignmentSection');
            const container = document.getElementById('assignmentList');
            currentMembers = members;

            if (needsReview.length === 0) {
                section.style.display = 'block';
                container.innerHTML = '<div style="text-align:center;padding:30px;color:#6b7280;">No unassigned PRs needing review for your team filters.</div>';
                return;
            }

            // Build load map
            const loadMap = {};
            members.forEach(m => {
                const p = findProfile(m);
                loadMap[m] = p ? p.open_reviews : 0;
            });

            // Round-robin assign: lowest load gets next PR
            currentAssignments = {};
            needsReview.forEach((pr, idx) => {
                const eligible = members.filter(m => m.toLowerCase() !== pr.author.toLowerCase());
                if (eligible.length === 0) return;
                eligible.sort((a, b) => loadMap[a] - loadMap[b]);
                const assignee = eligible[0];
                currentAssignments[idx] = {pr, assignee, included: true};
                loadMap[assignee]++;
            });

            rebuildAssignmentUI(members, loadMap);
            section.style.display = 'block';
        }

        function rebuildAssignmentUI(members, loadMap) {
            const container = document.getElementById('assignmentList');

            // Group by assignee
            const grouped = {};
            members.forEach(m => grouped[m] = []);
            Object.entries(currentAssignments).forEach(([idx, item]) => {
                if (!grouped[item.assignee]) grouped[item.assignee] = [];
                grouped[item.assignee].push({...item, idx: parseInt(idx)});
            });

            // Recalculate load
            if (!loadMap) {
                loadMap = {};
                members.forEach(m => {
                    const p = findProfile(m);
                    loadMap[m] = p ? p.open_reviews : 0;
                });
                Object.values(currentAssignments).forEach(item => {
                    if (item.included) loadMap[item.assignee] = (loadMap[item.assignee] || 0) + 1;
                });
            }

            const memberOptions = members.map(m => `<option value="${m}">${m}</option>`).join('');

            let html = `<div style="margin-bottom: 15px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                <button id="assignBtn" onclick="assignOnGitHub()" style="padding: 10px 20px; background: #16a34a; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; min-height: 44px;">🚀 Assign on GitHub</button>
                <button onclick="copyAssignCommands()" style="padding: 10px 20px; background: #667eea; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 500; min-height: 44px;">📋 Copy Commands</button>
                <span id="copyStatus" style="color: #6b7280; font-size: 0.85em;"></span>
            </div>
            <div id="assignStatus" aria-live="polite" role="status" style="margin-bottom: 15px; line-height: 1.8;"></div>`;

            members.forEach(m => {
                const items = grouped[m] || [];
                const includedCount = items.filter(i => i.included).length;
                const totalLoad = (findProfile(m)?.open_reviews || 0) + includedCount;
                const color = getLoadColor(totalLoad);

                html += `<div style="margin-bottom: 20px; background: #f8f9fa; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb;">
                    <div style="padding: 12px 16px; background: white; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span role="img" aria-label="Load status: ${getStatusLabel(totalLoad)}" style="display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;background:${color};"></span>
                            <strong style="font-size: 1.1em;">${esc(m)}</strong>
                            <span style="color:#6b7280;margin-left:8px;">${includedCount} PR${includedCount !== 1 ? 's' : ''} to review</span>
                        </div>
                        <span style="font-size:0.85em;color:#6b7280;">Final load: ${totalLoad} PRs</span>
                    </div>`;

                if (items.length === 0) {
                    html += '<div style="padding:12px 16px;color:#10b981;font-size:0.9em;">✅ No new PRs assigned (already at capacity)</div>';
                } else {
                    html += '<table style="width:100%;border-collapse:collapse;font-size:0.9em;">';
                    items.forEach(item => {
                        const pr = item.pr;
                        const checkedAttr = item.included ? 'checked' : '';
                        const rowOpacity = item.included ? '1' : '0.4';

                        // Get PR size
                        const sizeLabel = pr.labels?.find(l => l.startsWith('size/'));
                        const size = sizeLabel ? sizeLabel.replace('size/', '') : 'M';
                        const sizeColors = { XS: '#10b981', S: '#3b82f6', M: '#f59e0b', L: '#ef4444', XL: '#dc2626' };
                        const sizeColor = sizeColors[size] || '#6b7280';

                        html += `<tr style="border-bottom:1px solid #e5e7eb;opacity:${rowOpacity};">
                            <td style="padding:8px 8px 8px 16px;width:30px;"><input type="checkbox" ${checkedAttr} onchange="togglePR(${item.idx}, this.checked)" aria-label="Include PR #${pr.number} in assignment" style="width:18px;height:18px;cursor:pointer;"></td>
                            <td style="padding:8px 4px;width:90px;">
                                <a href="${pr.url}" target="_blank" style="color:#667eea;font-weight:500;text-decoration:none;">#${pr.number}</a>
                                <span style="background:${sizeColor};color:white;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:3px;font-weight:600;">${size}</span>
                            </td>
                            <td style="padding:8px 4px;" title="${esc(pr.title)}">${esc(pr.title.substring(0, 70))}${pr.title.length > 70 ? '...' : ''}</td>
                            <td style="padding:8px;width:100px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#6b7280;" title="${esc(pr.author)}">${esc(pr.author)}</td>
                            <td style="padding:8px;width:80px;"><span style="background:${getSquadColor(pr.squad)};color:white;padding:2px 6px;border-radius:8px;font-size:0.8em;font-weight:500;">${esc(pr.squad)}</span></td>
                            <td style="padding:8px;width:50px;${getAgeStyle(pr.age)}">${pr.age}d</td>
                            <td style="padding:8px;width:150px;">
                                <select onchange="reassignPR(${item.idx}, this.value)" style="padding:4px 8px;border:1px solid #d1d5db;border-radius:4px;font-size:0.9em;cursor:pointer;width:140px;">
                                    ${members.map(om => `<option value="${esc(om)}" ${om === item.assignee ? 'selected' : ''}>${esc(om)}</option>`).join('')}
                                </select>
                            </td>
                        </tr>`;
                    });
                    html += '</table>';
                }
                html += '</div>';
            });

            container.innerHTML = html;
        }

        function togglePR(idx, included) {
            currentAssignments[idx].included = included;
            rebuildAssignmentUI(currentMembers);
        }

        function reassignPR(idx, newAssignee) {
            currentAssignments[idx].assignee = newAssignee;
            rebuildAssignmentUI(currentMembers);
        }

        function copyAssignCommands() {
            const commands = [];
            Object.values(currentAssignments).forEach(item => {
                if (!item.included) return;
                const p = findProfile(item.assignee);
                const login = p ? p.login : item.assignee;
                commands.push(`gh pr edit ${item.pr.number} --add-reviewer ${login}`);
            });

            if (commands.length === 0) {
                document.getElementById('copyStatus').textContent = 'No PRs selected';
                return;
            }

            const text = commands.join(String.fromCharCode(10));
            navigator.clipboard.writeText(text).then(() => {
                document.getElementById('copyStatus').textContent = `Copied ${commands.length} commands!`;
                setTimeout(() => { document.getElementById('copyStatus').textContent = ''; }, 3000);
            }).catch(() => {
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:600px;height:300px;z-index:10001;padding:15px;font-family:monospace;font-size:0.9em;border:2px solid #667eea;border-radius:8px;';
                const overlay = document.createElement('div');
                overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:10000;';
                overlay.onclick = () => { overlay.remove(); ta.remove(); };
                document.body.appendChild(overlay);
                document.body.appendChild(ta);
                ta.select();
                document.getElementById('copyStatus').textContent = 'Select all and copy from the textbox';
            });
        }

        function saveGHToken() {
            const token = document.getElementById('ghTokenInput').value.trim();
            if (!token) return;
            storage.set('prDashboardGHToken', token);
            document.getElementById('tokenStatus').textContent = 'Token saved!';
            setTimeout(() => { document.getElementById('tokenStatus').textContent = ''; }, 2000);
        }

        function loadGHToken() {
            const token = storage.get('prDashboardGHToken') || '';
            document.getElementById('ghTokenInput').value = token;
        }

        async function assignOnGitHub() {
            const token = document.getElementById('ghTokenInput').value.trim() || storage.get('prDashboardGHToken');
            if (!token) {
                showNotification('Please enter your GitHub token first');
                return;
            }
            storage.set('prDashboardGHToken', token);

            const selected = Object.values(currentAssignments).filter(item => item.included);
            if (selected.length === 0) {
                showNotification('No PRs selected');
                return;
            }

            if (!confirm(`Assign reviewers to ${selected.length} PR${selected.length !== 1 ? 's' : ''} on GitHub?`)) {
                return;
            }

            const btn = document.getElementById('assignBtn');
            btn.disabled = true;
            btn.textContent = 'Assigning...';
            const statusEl = document.getElementById('assignStatus');
            statusEl.innerHTML = '';

            let success = 0;
            let failed = 0;

            for (const item of selected) {
                const p = findProfile(item.assignee);
                const login = p ? p.login : item.assignee;
                try {
                    const resp = await fetch(
                        `https://api.github.com/repos/red-hat-storage/ocs-ci/pulls/${item.pr.number}/requested_reviewers`,
                        {
                            method: 'POST',
                            headers: {
                                'Authorization': `Bearer ${token}`,
                                'Accept': 'application/vnd.github+json',
                                'Content-Type': 'application/json'
                            },
                            body: JSON.stringify({reviewers: [login]})
                        }
                    );
                    if (resp.ok) {
                        success++;
                        statusEl.innerHTML += `<span style="color:#16a34a;font-size:0.85em;">&#10003; #${item.pr.number} → ${esc(login)}  </span>`;
                    } else {
                        const err = await resp.json();
                        failed++;
                        statusEl.innerHTML += `<span style="color:#dc2626;font-size:0.85em;">&#10007; #${item.pr.number}: ${esc(err.message || String(resp.status))}  </span>`;
                    }
                } catch (e) {
                    failed++;
                    statusEl.innerHTML += `<span style="color:#dc2626;font-size:0.85em;">&#10007; #${item.pr.number}: ${esc(e.message)}  </span>`;
                }
            }

            btn.disabled = false;
            btn.textContent = 'Assign on GitHub';
            var summary = `Assigned ${success} PR${success !== 1 ? 's' : ''}${failed > 0 ? `, ${failed} failed` : ''}`;
            statusEl.setAttribute('aria-label', summary);
            statusEl.setAttribute('tabindex', '-1');
            statusEl.focus();
            showNotification(summary);
        }

        // Auto-save team settings as user types
        function autoSaveTeamSettings() {
            const membersInput = document.getElementById('teamMembersInput').value.trim();
            const filterInput = document.getElementById('teamFilterInput').value.trim();
            storage.set('prDashboardTeam', membersInput);
            storage.set('prDashboardTeamFilter', filterInput);
        }

        // Toggle workload section
        function toggleWorkload() {
            const content = document.getElementById('workloadContent');
            const button = document.getElementById('workloadToggle');

            if (content.style.display === 'none') {
                content.style.display = 'block';
                button.textContent = '▲ Hide';
                storage.set('prDashboardWorkloadOpen', 'true');
                const savedTeam = storage.get('prDashboardTeam') || defaultTeam.join(', ');
                const savedFilter = storage.get('prDashboardTeamFilter') || '';
                document.getElementById('teamMembersInput').value = savedTeam;
                document.getElementById('teamFilterInput').value = savedFilter;
                loadGHToken();
                var hint = document.getElementById('onboardingHint');
                if (!savedTeam || savedTeam.trim() === '') {
                    hint.style.display = 'block';
                } else {
                    hint.style.display = 'none';
                    analyzeTeam();
                }
            } else {
                content.style.display = 'none';
                button.textContent = '▼ Show';
                storage.set('prDashboardWorkloadOpen', 'false');
            }
        }

        // Toggle analytics section
        function toggleAnalytics() {
            const content = document.getElementById('analyticsContent');
            const button = document.getElementById('analyticsToggle');
            
            if (content.style.display === 'none') {
                content.style.display = 'block';
                button.textContent = '▲ Hide Charts';
                // Initialize charts when shown
                if (!window.chartsInitialized) {
                    initializeCharts();
                    window.chartsInitialized = true;
                }
            } else {
                content.style.display = 'none';
                button.textContent = '▼ Show Charts';
            }
        }
        
        // Initialize charts
        function initializeCharts() {
            if (typeof Chart === 'undefined') {
                document.getElementById('analyticsContent').innerHTML = '<div style="text-align:center;padding:40px;color:#6b7280;">Charts unavailable — Chart.js failed to load. Check your network connection and reload.</div>';
                return;
            }
            const analytics = {{ analytics|tojson }};

            // Add dates to week labels
            function addDatesToWeekLabels(labels) {
                const today = new Date();
                return labels.map((label, index) => {
                    const weeksAgo = labels.length - 1 - index;
                    const endDate = new Date(today);
                    endDate.setDate(today.getDate() - (weeksAgo * 7));
                    const startDate = new Date(endDate);
                    startDate.setDate(endDate.getDate() - 6);

                    const formatDate = (date) => {
                        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                        return `${months[date.getMonth()]} ${date.getDate()}`;
                    };

                    return `${label} (${formatDate(startDate)} - ${formatDate(endDate)})`;
                });
            }

            // PR Volume Trend Chart
            if (analytics.volume_trend && analytics.volume_trend.data.length > 0) {
                const volumeCtx = document.getElementById('volumeChart').getContext('2d');
                new Chart(volumeCtx, {
                    type: 'line',
                    data: {
                        labels: addDatesToWeekLabels(analytics.volume_trend.labels),
                        datasets: [{
                            label: 'Open PRs',
                            data: analytics.volume_trend.data,
                            borderColor: '#667eea',
                            backgroundColor: 'rgba(102, 126, 234, 0.1)',
                            borderWidth: 3,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 5,
                            pointHoverRadius: 7
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'top'
                            },
                            tooltip: {
                                mode: 'index',
                                intersect: false
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: {
                                    stepSize: 10
                                }
                            }
                        }
                    }
                });
            }
            
            // Status Distribution Chart
            const statusCtx = document.getElementById('statusChart').getContext('2d');
            new Chart(statusCtx, {
                type: 'doughnut',
                data: {
                    labels: analytics.status_distribution.labels,
                    datasets: [{
                        data: analytics.status_distribution.data,
                        backgroundColor: analytics.status_distribution.colors,
                        borderWidth: 2,
                        borderColor: '#fff'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            position: 'bottom'
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    const label = context.label || '';
                                    const value = context.parsed || 0;
                                    const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                    const percentage = ((value / total) * 100).toFixed(1);
                                    return label + ': ' + value + ' (' + percentage + '%)';
                                }
                            }
                        }
                    }
                }
            });
            
            // Age Distribution Chart
            const ageCtx = document.getElementById('ageChart').getContext('2d');
            const ageChart = new Chart(ageCtx, {
                type: 'bar',
                data: {
                    labels: analytics.age_distribution.labels,
                    datasets: [{
                        label: 'Number of PRs',
                        data: analytics.age_distribution.data,
                        backgroundColor: analytics.age_distribution.colors,
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return 'PRs: ' + context.parsed.y + ' (click to filter)';
                                }
                            }
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                stepSize: 10
                            }
                        }
                    },
                    onClick: (event, activeElements) => {
                        if (activeElements.length > 0) {
                            const index = activeElements[0].index;
                            const ageRange = analytics.age_distribution.labels[index];
                            filterByAgeRange(ageRange);
                        }
                    }
                }
            });
        }
        
        // Smart auto-refresh: only if user is idle and not actively working
        let lastActivityTime = Date.now();

        // Track user activity
        ['click', 'keydown', 'input', 'change'].forEach(event => {
            document.addEventListener(event, () => {
                lastActivityTime = Date.now();
            });
        });

        // Check every minute if we should refresh
        setInterval(() => {
            const idleMinutes = (Date.now() - lastActivityTime) / (60 * 1000);
            const workloadOpen = document.getElementById('workloadContent')?.style.display !== 'none';
            const hasUnsavedAssignments = Object.keys(currentAssignments || {}).length > 0;

            // Only refresh if:
            // - User has been idle for 3+ minutes
            // - Workload section is closed OR no unsaved assignments
            if (idleMinutes >= 3 && (!workloadOpen || !hasUnsavedAssignments)) {
                saveFiltersToSession();
                showNotification('Refreshing dashboard with latest PR data...');
                setTimeout(() => location.reload(), 1000);
            }
        }, 60 * 1000); // Check every minute
    </script>
</body>
</html>
    ''')
    
    # Format timestamp
    generated_at = datetime.fromisoformat(pr_data['generated_at']).strftime('%Y-%m-%d %H:%M UTC')
    
    html = template.render(
        by_squad=pr_data['by_squad'],
        by_status=pr_data['by_status'],
        stale_prs=pr_data['stale_prs'],
        summary=pr_data['summary'],
        all_labels=pr_data['all_labels'],
        all_branches=pr_data['all_branches'],
        generated_at=generated_at,
        analytics=pr_data.get('analytics', {}),
        reviewer_profiles=pr_data.get('reviewer_profiles', {}),
        default_team=pr_data.get('default_team', [])
    )
    
    return html

def add_analytics_data(pr_data):
    """Add analytics data for charts"""
    from collections import defaultdict
    
    # Calculate status distribution
    summary = pr_data['summary']
    status_distribution = {
        'labels': ['Needs Review', 'Needs Changes', 'Approved'],
        'data': [
            summary['waiting_reviewer'],
            summary['waiting_author'],
            summary['approved']
        ],
        'colors': ['#f59e0b', '#ef4444', '#10b981']
    }
    
    # Calculate age distribution
    age_buckets = {
        '0-2 days': 0,
        '3-7 days': 0,
        '8-14 days': 0,
        '15-30 days': 0,
        '30-60 days': 0,
        '60+ days': 0
    }
    
    for squad_prs in pr_data['by_squad'].values():
        for pr in squad_prs:
            age = pr['age_days']
            if age <= 2:
                age_buckets['0-2 days'] += 1
            elif age <= 7:
                age_buckets['3-7 days'] += 1
            elif age <= 14:
                age_buckets['8-14 days'] += 1
            elif age <= 30:
                age_buckets['15-30 days'] += 1
            elif age <= 60:
                age_buckets['30-60 days'] += 1
            else:
                age_buckets['60+ days'] += 1
    
    age_distribution = {
        'labels': list(age_buckets.keys()),
        'data': list(age_buckets.values()),
        'colors': ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#991b1b', '#7f1d1d']
    }
    
    # Load and calculate volume trend
    history_file = 'docs/dashboard_history.json'
    history = {'snapshots': []}
    
    if os.path.exists(history_file):
        with open(history_file, 'r') as f:
            history = json.load(f)
    
    # Add current snapshot
    snapshot = {
        'timestamp': pr_data['generated_at'],
        'total_open': summary['total_open'],
        'waiting_reviewer': summary['waiting_reviewer'],
        'waiting_author': summary['waiting_author'],
        'approved': summary['approved']
    }
    history['snapshots'].append(snapshot)
    
    # Keep only last 30 days
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
    history['snapshots'] = [
        s for s in history['snapshots']
        if datetime.fromisoformat(s['timestamp'].replace('Z', '+00:00')) > cutoff_date
    ]
    
    # Save history
    with open(history_file, 'w') as f:
        json.dump(history, f, indent=2)
    
    # Calculate volume trend (last 4 weeks)
    weekly_data = defaultdict(int)
    for snapshot in history['snapshots']:
        timestamp = datetime.fromisoformat(snapshot['timestamp'].replace('Z', '+00:00'))
        week_key = timestamp.strftime('%Y-W%U')
        weekly_data[week_key] = snapshot['total_open']
    
    sorted_weeks = sorted(weekly_data.keys())[-4:]
    volume_trend = {
        'labels': [f"Week {i+1}" for i in range(len(sorted_weeks))],
        'data': [weekly_data[week] for week in sorted_weeks] if sorted_weeks else []
    }
    
    # Add analytics to pr_data
    pr_data['analytics'] = {
        'status_distribution': status_distribution,
        'age_distribution': age_distribution,
        'volume_trend': volume_trend
    }
    
    return pr_data


def main():
    """Main function"""
    # Get environment variables and clean them aggressively
    token = os.environ.get('GITHUB_TOKEN', '')
    repo_name = os.environ.get('REPO_NAME', '')

    # Remove ALL whitespace characters (spaces, tabs, newlines, etc.)
    token = ''.join(token.split())
    repo_name = repo_name.strip()

    # Check if user accidentally included "token" prefix
    if token.lower().startswith('token'):
        token = token[5:].strip()  # Remove "token" prefix
        print("Info: Removed 'token' prefix from GITHUB_TOKEN")

    # Validate token format
    if token and not token.startswith('ghp_') and not token.startswith('github_pat_'):
        print(f"Warning: Token doesn't look like a valid GitHub token")
        print(f"Expected format: ghp_xxxx or github_pat_xxxx")
        print(f"Got: {token[:15]}... (length: {len(token)})")

    if not token or not repo_name:
        print("Error: GITHUB_TOKEN and REPO_NAME environment variables required")
        return
    
    print(f"Collecting PR data from {repo_name}...")
    pr_data = collect_pr_data(repo_name, token)
    
    print(f"Found {pr_data['summary']['total_open']} open PRs")
    print(f"  - Waiting on reviewer: {pr_data['summary']['waiting_reviewer']}")
    print(f"  - Waiting on author: {pr_data['summary']['waiting_author']}")
    print(f"  - Approved: {pr_data['summary']['approved']}")
    print(f"  - Stale: {pr_data['summary']['stale']}")
    
    # Add analytics data
    print("\nAdding analytics data...")
    pr_data = add_analytics_data(pr_data)

    # Build reviewer profiles (all users — filtering happens client-side)
    print("Building reviewer profiles...")
    reviewer_profiles = build_reviewer_profiles(pr_data)
    pr_data['reviewer_profiles'] = reviewer_profiles
    pr_data['default_team'] = DEFAULT_TEAM_MEMBERS

    # Save JSON data
    print("Saving dashboard data...")
    os.makedirs('docs', exist_ok=True)
    with open('docs/dashboard_data.json', 'w') as f:
        json.dump(pr_data, f, indent=2)
    
    # Generate HTML dashboard
    print("Generating HTML dashboard...")
    html = generate_html_dashboard(pr_data)
    
    with open('docs/pr_dashboard.html', 'w') as f:
        f.write(html)
    
    print("✅ Dashboard generated successfully!")
    print("   - Data: docs/dashboard_data.json")
    print("   - Dashboard: docs/pr_dashboard.html")


if __name__ == '__main__':
    main()

