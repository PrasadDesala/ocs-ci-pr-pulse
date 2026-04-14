#!/usr/bin/env python3
"""
Simple script to view the PR dashboard in your browser.
Works for private repositories without needing GitHub Pages.
"""

import webbrowser
import os
from pathlib import Path

def main():
    # Get the absolute path to the dashboard
    dashboard_path = Path(__file__).parent / 'docs' / 'pr_dashboard.html'
    
    if not dashboard_path.exists():
        print("❌ Dashboard not found!")
        print(f"   Expected location: {dashboard_path}")
        print("\n💡 Generate the dashboard first:")
        print("   1. For demo data: python generate_demo_dashboard.py")
        print("   2. For real data: python .github/scripts/generate_dashboard.py")
        return 1
    
    # Convert to file:// URL
    dashboard_url = dashboard_path.as_uri()
    
    print("🚀 Opening OCS-CI PR Pulse Dashboard...")
    print(f"📊 Location: {dashboard_path}")
    print(f"🔗 URL: {dashboard_url}")
    
    # Open in default browser
    webbrowser.open(dashboard_url)
    
    print("\n✅ Dashboard opened in your browser!")
    print("\nIf the browser didn't open automatically, copy this URL:")
    print(f"   {dashboard_url}")
    
    return 0

if __name__ == '__main__':
    exit(main())

# Made with Bob
