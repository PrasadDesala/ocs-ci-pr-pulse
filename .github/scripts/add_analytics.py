#!/usr/bin/env python3
"""
Add analytics data to dashboard
Calculates historical trends, status distribution, and age distribution
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

def load_historical_data():
    """Load historical data from previous runs"""
    history_file = 'docs/dashboard_history.json'
    
    if os.path.exists(history_file):
        with open(history_file, 'r') as f:
            return json.load(f)
    
    return {'snapshots': []}

def save_historical_snapshot(pr_data):
    """Save current snapshot to history"""
    history = load_historical_data()
    
    # Create snapshot
    snapshot = {
        'timestamp': pr_data['generated_at'],
        'total_open': pr_data['summary']['total_open'],
        'waiting_reviewer': pr_data['summary']['waiting_reviewer'],
        'waiting_author': pr_data['summary']['waiting_author'],
        'approved': pr_data['summary']['approved'],
        'stale': pr_data['summary']['stale'],
        'draft': pr_data['summary']['draft']
    }
    
    # Add to history
    history['snapshots'].append(snapshot)
    
    # Keep only last 30 days of data
    cutoff_date = datetime.now() - timedelta(days=30)
    history['snapshots'] = [
        s for s in history['snapshots']
        if datetime.fromisoformat(s['timestamp'].replace('Z', '+00:00')) > cutoff_date
    ]
    
    # Save history
    with open('docs/dashboard_history.json', 'w') as f:
        json.dump(history, f, indent=2)
    
    return history

def calculate_status_distribution(pr_data):
    """Calculate status distribution for pie chart"""
    summary = pr_data['summary']
    
    return {
        'labels': ['Needs Review', 'Needs Changes', 'Approved'],
        'data': [
            summary['waiting_reviewer'],
            summary['waiting_author'],
            summary['approved']
        ],
        'colors': ['#f59e0b', '#ef4444', '#10b981']
    }

def calculate_age_distribution(pr_data):
    """Calculate age distribution for histogram"""
    age_buckets = {
        '0-2 days': 0,
        '3-7 days': 0,
        '8-14 days': 0,
        '15-30 days': 0,
        '30+ days': 0
    }
    
    # Count PRs in each bucket
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
            else:
                age_buckets['30+ days'] += 1
    
    return {
        'labels': list(age_buckets.keys()),
        'data': list(age_buckets.values()),
        'colors': ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#991b1b']
    }

def calculate_volume_trend(history):
    """Calculate PR volume trend for line chart"""
    if not history['snapshots']:
        return {
            'labels': [],
            'data': []
        }
    
    # Group by week
    weekly_data = defaultdict(int)
    
    for snapshot in history['snapshots']:
        timestamp = datetime.fromisoformat(snapshot['timestamp'].replace('Z', '+00:00'))
        week_key = timestamp.strftime('%Y-W%U')  # Year-Week format
        weekly_data[week_key] = snapshot['total_open']
    
    # Get last 4 weeks
    sorted_weeks = sorted(weekly_data.keys())[-4:]
    
    return {
        'labels': [f"Week {i+1}" for i in range(len(sorted_weeks))],
        'data': [weekly_data[week] for week in sorted_weeks],
        'raw_labels': sorted_weeks
    }

def add_analytics_to_dashboard(pr_data):
    """Add analytics data to PR data"""
    
    # Save historical snapshot
    history = save_historical_snapshot(pr_data)
    
    # Calculate distributions
    pr_data['analytics'] = {
        'status_distribution': calculate_status_distribution(pr_data),
        'age_distribution': calculate_age_distribution(pr_data),
        'volume_trend': calculate_volume_trend(history)
    }
    
    return pr_data

def main():
    """Main function to add analytics to existing dashboard data"""
    
    # Load current dashboard data
    try:
        with open('docs/dashboard_data.json', 'r') as f:
            pr_data = json.load(f)
    except FileNotFoundError:
        print("❌ Dashboard data not found. Run generate_dashboard.py first.")
        return
    
    print("📊 Adding analytics data...")
    
    # Add analytics
    pr_data = add_analytics_to_dashboard(pr_data)
    
    # Save updated data
    with open('docs/dashboard_data.json', 'w') as f:
        json.dump(pr_data, f, indent=2)
    
    print("✅ Analytics data added successfully!")
    print(f"   - Status distribution: {len(pr_data['analytics']['status_distribution']['data'])} categories")
    print(f"   - Age distribution: {len(pr_data['analytics']['age_distribution']['data'])} buckets")
    print(f"   - Volume trend: {len(pr_data['analytics']['volume_trend']['data'])} data points")

if __name__ == '__main__':
    main()

