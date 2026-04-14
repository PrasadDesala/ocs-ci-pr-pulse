#!/usr/bin/env python3
"""Generate a demo dashboard with sample data"""

import json
import sys
import os
from datetime import datetime, timedelta

# Add the scripts directory to path to import the HTML generator
sys.path.insert(0, '.github/scripts')
from generate_dashboard import generate_html_dashboard

def generate_demo_data():
    """Generate sample PR data for demo purposes"""
    now = datetime.now()
    
    # Create sample PRs with various states
    sample_prs = []
    
    squads = ['Team Alpha', 'Team Beta', 'Team Gamma']
    sizes = ['XS', 'S', 'M', 'L', 'XL']
    
    # Waiting on reviewer PRs
    for i in range(5):
        sample_prs.append({
            'number': 14900 + i,
            'title': f'Add new feature for component {i+1}',
            'author': f'developer{i+1}',
            'created_at': (now - timedelta(days=i+2)).isoformat(),
            'updated_at': (now - timedelta(days=i)).isoformat(),
            'status': 'waiting_reviewer',
            'labels': ['enhancement', 'needs-review'],
            'is_draft': False,
            'is_stale': False,
            'age_days': i + 2,
            'url': f'https://github.com/red-hat-storage/ocs-ci/pull/{14900+i}',
            'squad': squads[i % len(squads)],
            'size': sizes[i % len(sizes)],
            'additions': 50 + i * 20,
            'deletions': 10 + i * 5,
            'reviewers': [f'reviewer{j+1}' for j in range(2)],
            'comments': i + 1,
            'review_comments': i * 2
        })
    
    # Waiting on author PRs
    for i in range(3):
        sample_prs.append({
            'number': 14850 + i,
            'title': f'Fix bug in module {i+1}',
            'author': f'contributor{i+1}',
            'created_at': (now - timedelta(days=i+5)).isoformat(),
            'updated_at': (now - timedelta(days=i+1)).isoformat(),
            'status': 'waiting_author',
            'labels': ['bug', 'changes-requested'],
            'is_draft': False,
            'is_stale': False,
            'age_days': i + 5,
            'url': f'https://github.com/red-hat-storage/ocs-ci/pull/{14850+i}',
            'squad': squads[i % len(squads)],
            'size': sizes[(i+1) % len(sizes)],
            'additions': 30 + i * 15,
            'deletions': 20 + i * 10,
            'reviewers': [f'reviewer{j+1}' for j in range(1)],
            'comments': i + 3,
            'review_comments': i * 3
        })
    
    # Approved PRs
    for i in range(4):
        sample_prs.append({
            'number': 14800 + i,
            'title': f'Update documentation for {i+1}',
            'author': f'docs-team{i+1}',
            'created_at': (now - timedelta(days=i+3)).isoformat(),
            'updated_at': (now - timedelta(hours=i+1)).isoformat(),
            'status': 'approved',
            'labels': ['documentation', 'approved'],
            'is_draft': False,
            'is_stale': False,
            'age_days': i + 3,
            'url': f'https://github.com/red-hat-storage/ocs-ci/pull/{14800+i}',
            'squad': squads[i % len(squads)],
            'size': 'XS',
            'additions': 10 + i * 5,
            'deletions': 5 + i * 2,
            'reviewers': [f'reviewer{j+1}' for j in range(2)],
            'comments': i + 2,
            'review_comments': 0
        })
    
    # Stale PRs
    for i in range(3):
        sample_prs.append({
            'number': 14700 + i,
            'title': f'Old PR: Refactor legacy code {i+1}',
            'author': f'olddev{i+1}',
            'created_at': (now - timedelta(days=45+i)).isoformat(),
            'updated_at': (now - timedelta(days=35+i)).isoformat(),
            'status': 'waiting_reviewer',
            'labels': ['refactoring', 'stale'],
            'is_draft': False,
            'is_stale': True,
            'age_days': 45 + i,
            'url': f'https://github.com/red-hat-storage/ocs-ci/pull/{14700+i}',
            'squad': 'Unassigned',
            'size': 'L',
            'additions': 200 + i * 50,
            'deletions': 100 + i * 30,
            'reviewers': [],
            'comments': i + 1,
            'review_comments': i
        })
    
    # Draft PRs
    for i in range(2):
        sample_prs.append({
            'number': 14600 + i,
            'title': f'WIP: New experimental feature {i+1}',
            'author': f'researcher{i+1}',
            'created_at': (now - timedelta(days=i+7)).isoformat(),
            'updated_at': (now - timedelta(days=i+2)).isoformat(),
            'status': 'draft',
            'labels': ['WIP', 'experimental'],
            'is_draft': True,
            'is_stale': False,
            'age_days': i + 7,
            'url': f'https://github.com/red-hat-storage/ocs-ci/pull/{14600+i}',
            'squad': squads[i % len(squads)],
            'size': 'M',
            'additions': 100 + i * 30,
            'deletions': 50 + i * 20,
            'reviewers': [],
            'comments': 0,
            'review_comments': 0
        })
    
    # Group PRs by status
    by_status = {
        'waiting_reviewer': [pr for pr in sample_prs if pr['status'] == 'waiting_reviewer' and not pr['is_draft']],
        'waiting_author': [pr for pr in sample_prs if pr['status'] == 'waiting_author'],
        'approved': [pr for pr in sample_prs if pr['status'] == 'approved'],
        'draft': [pr for pr in sample_prs if pr['is_draft']]
    }
    
    # Get stale PRs
    stale_prs = [pr for pr in sample_prs if pr['is_stale']]
    
    # Collect all unique labels
    all_labels = set()
    for pr in sample_prs:
        all_labels.update(pr['labels'])
    
    # Create demo squad data
    by_squad = {
        'Team Alpha': [pr for pr in sample_prs[:5]],
        'Team Beta': [pr for pr in sample_prs[5:10]],
        'Unassigned': [pr for pr in sample_prs[10:]]
    }
    
    # Create the data structure
    pr_data = {
        'generated_at': now.isoformat(),
        'repository': 'red-hat-storage/ocs-ci',
        'summary': {
            'total': len(sample_prs),
            'waiting_reviewer': sum(1 for pr in sample_prs if pr['status'] == 'waiting_reviewer'),
            'waiting_author': sum(1 for pr in sample_prs if pr['status'] == 'waiting_author'),
            'approved': sum(1 for pr in sample_prs if pr['status'] == 'approved'),
            'stale': sum(1 for pr in sample_prs if pr['is_stale']),
            'draft': sum(1 for pr in sample_prs if pr['is_draft'])
        },
        'prs': sample_prs,
        'by_status': by_status,
        'by_squad': by_squad,
        'stale_prs': stale_prs,
        'all_labels': sorted(list(all_labels))
    }
    
    return pr_data

def main():
    print("Generating demo dashboard data...")
    
    # Generate demo data
    pr_data = generate_demo_data()
    
    # Create docs directory
    os.makedirs('docs', exist_ok=True)
    
    # Save JSON data
    print("Saving demo dashboard data...")
    with open('docs/dashboard_data.json', 'w') as f:
        json.dump(pr_data, f, indent=2)
    
    # Generate HTML dashboard
    print("Generating demo HTML dashboard...")
    html = generate_html_dashboard(pr_data)
    
    with open('docs/pr_dashboard.html', 'w') as f:
        f.write(html)
    
    print("\n✅ Demo dashboard generated successfully!")
    print(f"   Total PRs: {pr_data['summary']['total']}")
    print(f"   Waiting on reviewer: {pr_data['summary']['waiting_reviewer']}")
    print(f"   Waiting on author: {pr_data['summary']['waiting_author']}")
    print(f"   Approved: {pr_data['summary']['approved']}")
    print(f"   Stale: {pr_data['summary']['stale']}")
    print(f"   Draft: {pr_data['summary']['draft']}")
    print(f"\n📊 Open docs/pr_dashboard.html in your browser to view the dashboard")

if __name__ == '__main__':
    main()

