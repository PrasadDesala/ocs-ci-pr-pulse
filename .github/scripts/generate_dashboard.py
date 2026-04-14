#!/usr/bin/env python3
"""
PR Dashboard Generator
Generates an interactive HTML dashboard showing PR status across squads
"""

import os
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from github import Github, Auth
from jinja2 import Template

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
    """Determine if PR is waiting on reviewer or author"""
    reviews = list(pr.get_reviews())
    
    if not reviews:
        return 'waiting_reviewer'
    
    # List of known bot usernames (without [bot] suffix)
    BOT_USERNAMES = ['ocs-ci', 'openshift-ci', 'dependabot', 'renovate']
    
    # Filter out bot reviews and author's own reviews
    human_reviews = [
        r for r in reviews
        if not (
            '[bot]' in r.user.login.lower()
            or r.user.type == 'Bot'
            or r.user.login in BOT_USERNAMES
        )
        and r.user.login != pr.user.login
    ]
    
    if not human_reviews:
        return 'waiting_reviewer'
    
    # Get all reviews by type (excluding bots and author)
    changes_requested_reviews = [r for r in human_reviews if r.state == 'CHANGES_REQUESTED']
    approved_reviews = [r for r in human_reviews if r.state == 'APPROVED']
    commented_reviews = [r for r in human_reviews if r.state == 'COMMENTED']
    
    # Get commits for timestamp comparison
    commits = list(pr.get_commits())
    latest_commit = commits[-1] if commits else None
    
    # Priority 1: CHANGES_REQUESTED reviews
    if changes_requested_reviews:
        latest_change_request = changes_requested_reviews[-1]
        
        # Check if author has pushed commits after the latest change request
        if latest_commit and latest_commit.commit.author.date > latest_change_request.submitted_at:
            return 'waiting_reviewer'
        
        # Otherwise, waiting on author to address the changes
        return 'waiting_author'
    
    # Priority 2: COMMENTED reviews (reviewer left feedback without formal request)
    elif commented_reviews:
        latest_comment_review = commented_reviews[-1]
        
        # Check if review has body text OR inline review comments from humans
        has_body = bool(latest_comment_review.body)
        
        # Check for human inline review comments (filter out bots)
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
            # If author pushed commits after the comment, waiting on reviewer
            if latest_commit and latest_commit.commit.author.date > latest_comment_review.submitted_at:
                return 'waiting_reviewer'
            
            # Otherwise, author should address the comments
            return 'waiting_author'
    
    # Priority 3: APPROVED reviews
    if approved_reviews:
        return 'approved'
    
    # Default: waiting on reviewer (no meaningful reviews yet)
    return 'waiting_reviewer'


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
    
    total_age = 0
    processed = 0
    
    for idx, pr in enumerate(open_prs, 1):
        # Process ALL PRs including drafts
        
        processed += 1
        if processed % 10 == 0 or processed == 1:
            print(f"Processing PR {idx}/{total_prs} (#{pr.number})...")
        
        age_days = get_pr_age_days(pr)
        status = get_pr_status(pr)
        squad = determine_squad(pr, repo)
        stale = is_stale(pr)
        size = get_pr_size(pr)
        is_draft = is_draft_or_wip(pr)
        
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
            'is_draft': is_draft,
            'size': size,
            'additions': pr.additions,
            'deletions': pr.deletions,
            'reviewers': [r.login for r in pr.requested_reviewers],
            'labels': [l.name for l in pr.labels],
            'comments': pr.comments,
            'review_comments': pr.review_comments
        }
        
        # Collect all labels for filter dropdown
        for label in pr.labels:
            pr_data['all_labels'].add(label.name)
        
        # Add to squad bucket
        pr_data['by_squad'][squad].append(pr_info)
        
        # Add to status bucket
        pr_data['by_status'][status].append(pr_info)
        
        # Track stale PRs
        if stale:
            pr_data['stale_prs'].append(pr_info)
        
        # Track draft PRs (including WIP in title)
        if is_draft:
            pr_data['draft_prs'].append(pr_info)
            pr_data['summary']['draft'] += 1
        
        # Update summary
        pr_data['summary']['total_open'] += 1
        pr_data['summary'][status] += 1
        if stale:
            pr_data['summary']['stale'] += 1
        total_age += age_days
    
    # Calculate average age
    if pr_data['summary']['total_open'] > 0:
        pr_data['summary']['avg_age_days'] = round(
            total_age / pr_data['summary']['total_open'], 1
        )
    
    # Convert labels set to sorted list
    pr_data['all_labels'] = sorted(list(pr_data['all_labels']))
    
    print(f"\n✅ Processed {processed} PRs")
    print(f"   Total open: {pr_data['summary']['total_open']}")
    print(f"   Waiting reviewer: {pr_data['summary']['waiting_reviewer']}")
    print(f"   Waiting author: {pr_data['summary']['waiting_author']}")
    print(f"   Approved: {pr_data['summary']['approved']}")
    print(f"   Stale: {pr_data['summary']['stale']}")
    print(f"   Draft: {pr_data['summary']['draft']}")
    
    return pr_data


def generate_html_dashboard(pr_data):
    """Generate HTML dashboard from PR data"""
    
    template = Template('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
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
            color: #666;
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
        
        .card.age { border-top: 4px solid #8b5cf6; }
        .card.age .card-value { color: #8b5cf6; }
        
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
        
        .age-indicator {
            font-weight: 500;
        }
        
        .age-fresh { color: #10b981; }
        .age-normal { color: #f59e0b; }
        .age-old { color: #ef4444; }
        
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
            padding: 8px 12px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            font-size: 0.9em;
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
            padding: 8px 12px;
            cursor: pointer;
            border-bottom: 1px solid #f3f4f6;
        }
        
        .autocomplete-item:hover {
            background: #f3f4f6;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔍 OCS-CI PR Pulse</h1>
            <div class="subtitle">Real-time Pull Request Status Tracking</div>
            <div class="last-updated">Last Updated: {{ generated_at }}</div>
        </div>
        
        <div class="summary-cards">
            <div class="card total" onclick="filterByCard('all')" title="Total number of open pull requests">
                <div class="card-label">Total Open PRs</div>
                <div class="card-value">{{ summary.total_open }}</div>
            </div>
            <div class="card reviewer" onclick="filterByCard('waiting_reviewer')" title="Needs Review 🟡 - PR is ready for initial review OR author has addressed feedback (pushed new commits). Action: Reviewers should review">
                <div class="card-label">Needs Review</div>
                <div class="card-value">{{ summary.waiting_reviewer }}</div>
            </div>
            <div class="card author" onclick="filterByCard('waiting_author')" title="Needs Changes 🔴 - Reviewer requested changes OR left review comments (inline or body). Author hasn't pushed commits since review. Action: Author should address feedback">
                <div class="card-label">Needs Changes</div>
                <div class="card-value">{{ summary.waiting_author }}</div>
            </div>
            <div class="card approved" onclick="filterByCard('approved')" title="Approved 🟢 - PR has been approved by one or more reviewers. Ready to merge. Action: Merge when ready">
                <div class="card-label">Approved</div>
                <div class="card-value">{{ summary.approved }}</div>
            </div>
            <div class="card stale" onclick="filterByCard('stale')" title="PRs with no activity for more than 7 days">
                <div class="card-label">Stale (>7 days)</div>
                <div class="card-value">{{ summary.stale }}</div>
            </div>
            <div class="card draft" onclick="filterByCard('draft')" title="Draft or work-in-progress pull requests">
                <div class="card-label">Draft / WIP</div>
                <div class="card-value">{{ summary.draft }}</div>
            </div>
            <div class="card age" title="Average age of all open PRs in days">
                <div class="card-label">Avg PR Age (days)</div>
                <div class="card-value">{{ summary.avg_age_days }}</div>
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
            <div class="filter-group autocomplete-wrapper">
                <label>Label:</label>
                <input type="text" id="labelFilter" placeholder="Type to search labels..." autocomplete="off" oninput="filterLabels()">
                <div id="labelSuggestions" class="autocomplete-suggestions"></div>
            </div>
            <div class="filter-group">
                <button onclick="resetFilters()" style="padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em;">
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
                            <th onclick="sortTable(0)" style="cursor: pointer;">PR #</th>
                            <th onclick="sortTable(1)" style="cursor: pointer;">Title</th>
                            <th onclick="sortTable(2)" style="cursor: pointer;">Author</th>
                            <th onclick="sortTable(3)" style="cursor: pointer;">Squad</th>
                            <th onclick="sortTable(4)" style="cursor: pointer;">Status</th>
                            <th onclick="sortTable(5)" style="cursor: pointer;">Age</th>
                            <th onclick="sortTable(6)" style="cursor: pointer;">Size</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for squad, prs in by_squad.items()|sort %}
                            {% for pr in prs|sort(attribute='age_days', reverse=true) %}
                            <tr data-status="{{ pr.status }}" data-squad="{{ pr.squad }}" data-size="{{ pr.size }}" data-stale="{{ pr.is_stale|lower }}" data-draft="{{ pr.is_draft|lower }}" data-author="{{ pr.author }}" data-labels="{{ pr.labels|join(',') }}">
                                <td><a href="{{ pr.url }}" class="pr-link" target="_blank">#{{ pr.number }}</a></td>
                                <td>
                                    {{ pr.title }}
                                    {% if pr.is_draft %}<span class="badge draft">DRAFT</span>{% endif %}
                                    {% if pr.is_stale %}<span class="badge stale">STALE</span>{% endif %}
                                </td>
                                <td>{{ pr.author }}</td>
                                <td>{{ pr.squad }}</td>
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
        // Filter PRs based on current filter values
        function filterPRs() {
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const squadFilter = document.getElementById('squadFilter').value.toLowerCase();
            const statusFilter = document.getElementById('statusFilter').value;
            const sizeFilter = document.getElementById('sizeFilter').value;
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
                const isStale = row.dataset.stale === 'true';
                const isDraft = row.dataset.draft === 'true';
                const prLabels = row.dataset.labels ? row.dataset.labels.toLowerCase() : '';
                
                let show = true;
                
                // Search filter (searches in PR#, title, and author)
                // Remove # from search term for PR number matching
                if (searchTerm) {
                    const cleanSearchTerm = searchTerm.replace('#', '');
                    if (!prNumber.includes(cleanSearchTerm) &&
                        !prTitle.includes(searchTerm) &&
                        !prAuthor.includes(searchTerm)) {
                        show = false;
                    }
                }
                
                // Squad filter
                if (squadFilter && prSquad !== squadFilter) {
                    show = false;
                }
                
                // Status filter
                if (statusFilter && prStatus !== statusFilter) {
                    show = false;
                }
                
                // Size filter
                if (sizeFilter && prSize !== sizeFilter) {
                    show = false;
                }
                
                // Label filter - check if any label matches
                if (labelFilter && !prLabels.includes(labelFilter)) {
                    show = false;
                }
                
                row.style.display = show ? '' : 'none';
                if (show) visibleCount++;
            });
            
            // Update visible count
            document.getElementById('visibleCount').textContent = visibleCount + ' PRs';
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
            document.getElementById('searchInput').value = '';
            document.getElementById('squadFilter').value = '';
            document.getElementById('statusFilter').value = '';
            document.getElementById('sizeFilter').value = '';
            document.getElementById('labelFilter').value = '';
            document.getElementById('labelSuggestions').classList.remove('show');
            
            // Remove active class from all cards
            document.querySelectorAll('.card').forEach(card => {
                card.classList.remove('active');
            });
            document.querySelector('.card.total').classList.add('active');
            
            document.getElementById('tableTitle').textContent = 'All Open Pull Requests';
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
                `<div class="autocomplete-item" onclick="selectLabel('${label}')">${label}</div>`
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
        function sortTable(columnIndex) {
            const table = document.getElementById('prTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            // Toggle sort direction
            sortDirection[columnIndex] = !sortDirection[columnIndex];
            const ascending = sortDirection[columnIndex];
            
            rows.sort((a, b) => {
                let aVal = a.cells[columnIndex].textContent.trim();
                let bVal = b.cells[columnIndex].textContent.trim();
                
                // Handle numeric values (PR#, Age)
                if (columnIndex === 0) {
                    aVal = parseInt(aVal.replace('#', ''));
                    bVal = parseInt(bVal.replace('#', ''));
                } else if (columnIndex === 5) {
                    aVal = parseInt(aVal.replace('d', ''));
                    bVal = parseInt(bVal.replace('d', ''));
                }
                
                if (aVal < bVal) return ascending ? -1 : 1;
                if (aVal > bVal) return ascending ? 1 : -1;
                return 0;
            });
            
            // Re-append sorted rows
            rows.forEach(row => tbody.appendChild(row));
        }
        
        // Initialize - show all PRs
        window.onload = function() {
            filterByCard('all');
        };
        
        // Auto-refresh every 5 minutes
        setTimeout(() => {
            location.reload();
        }, 5 * 60 * 1000);
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
        generated_at=generated_at
    )
    
    return html


def main():
    """Main function"""
    # Get environment variables
    token = os.environ.get('GITHUB_TOKEN')
    repo_name = os.environ.get('REPO_NAME')
    
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
    
    # Save JSON data
    print("\nSaving dashboard data...")
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

# Made with Bob
