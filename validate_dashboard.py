#!/usr/bin/env python3
"""
Dashboard Validation Script
Tests all filter combinations and logic
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# Add the scripts directory to path
sys.path.insert(0, '.github/scripts')

def test_pr_status_logic():
    """Test the PR status detection logic"""
    print("\n" + "="*60)
    print("TEST 1: PR Status Detection Logic")
    print("="*60)
    
    # Mock PR class for testing
    class MockReview:
        def __init__(self, state, submitted_at):
            self.state = state
            self.submitted_at = submitted_at
            self.body = ""  # For COMMENTED reviews
    
    class MockCommit:
        def __init__(self, author_date):
            self.commit = type('obj', (object,), {'author': type('obj', (object,), {'date': author_date})()})()
    
    class MockPR:
        def __init__(self, reviews, commits):
            self._reviews = reviews
            self._commits = commits
            self.review_comments = 0  # Number of review comments
        
        def get_reviews(self):
            return self._reviews
        
        def get_commits(self):
            return self._commits
    
    # Import the function
    from generate_dashboard import get_pr_status
    
    # Test Case 1: No reviews
    print("\n✓ Test Case 1: No reviews")
    pr = MockPR([], [])
    status = get_pr_status(pr)
    assert status == 'waiting_reviewer', f"Expected 'waiting_reviewer', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 2: CHANGES_REQUESTED, no commits after
    print("\n✓ Test Case 2: CHANGES_REQUESTED, author hasn't responded")
    review_time = datetime.now(timezone.utc) - timedelta(days=1)
    commit_time = datetime.now(timezone.utc) - timedelta(days=2)  # Before review
    pr = MockPR(
        [MockReview('CHANGES_REQUESTED', review_time)],
        [MockCommit(commit_time)]
    )
    status = get_pr_status(pr)
    assert status == 'waiting_author', f"Expected 'waiting_author', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 3: CHANGES_REQUESTED, commits after (author responded)
    print("\n✓ Test Case 3: CHANGES_REQUESTED, author responded with commits")
    review_time = datetime.now(timezone.utc) - timedelta(days=2)
    commit_time = datetime.now(timezone.utc) - timedelta(days=1)  # After review
    pr = MockPR(
        [MockReview('CHANGES_REQUESTED', review_time)],
        [MockCommit(commit_time)]
    )
    status = get_pr_status(pr)
    assert status == 'waiting_reviewer', f"Expected 'waiting_reviewer', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 4: APPROVED
    print("\n✓ Test Case 4: PR is approved")
    pr = MockPR(
        [MockReview('APPROVED', datetime.now(timezone.utc))],
        []
    )
    status = get_pr_status(pr)
    assert status == 'approved', f"Expected 'approved', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 5: Multiple reviews, latest is CHANGES_REQUESTED
    print("\n✓ Test Case 5: Multiple reviews, latest needs changes")
    old_review = MockReview('APPROVED', datetime.now(timezone.utc) - timedelta(days=3))
    new_review = MockReview('CHANGES_REQUESTED', datetime.now(timezone.utc) - timedelta(days=1))
    pr = MockPR([old_review, new_review], [])
    status = get_pr_status(pr)
    assert status == 'waiting_author', f"Expected 'waiting_author', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 6: COMMENTED review (reviewer left comments without formal request)
    print("\n✓ Test Case 6: Reviewer left comments (COMMENTED state)")
    comment_review = MockReview('COMMENTED', datetime.now(timezone.utc) - timedelta(days=1))
    comment_review.body = "Please fix the typo in line 42"
    old_commit = MockCommit(datetime.now(timezone.utc) - timedelta(days=2))
    pr = MockPR([comment_review], [old_commit])
    pr.review_comments = 3  # Has review comments
    status = get_pr_status(pr)
    assert status == 'waiting_author', f"Expected 'waiting_author', got '{status}'"
    print(f"  Result: {status} ✅")
    
    # Test Case 7: COMMENTED review, author responded
    print("\n✓ Test Case 7: COMMENTED review, author responded with commits")
    comment_review = MockReview('COMMENTED', datetime.now(timezone.utc) - timedelta(days=2))
    comment_review.body = "Please fix the typo"
    new_commit = MockCommit(datetime.now(timezone.utc) - timedelta(days=1))  # After comment
    pr = MockPR([comment_review], [new_commit])
    pr.review_comments = 2
    status = get_pr_status(pr)
    assert status == 'waiting_reviewer', f"Expected 'waiting_reviewer', got '{status}'"
    print(f"  Result: {status} ✅")
    
    print("\n✅ All status detection tests passed!")


def test_squad_name_cleanup():
    """Test squad name suffix removal"""
    print("\n" + "="*60)
    print("TEST 2: Squad Name Cleanup")
    print("="*60)
    
    from generate_dashboard import determine_squad
    
    # Mock repo and PR
    class MockFile:
        def __init__(self, filename):
            self.filename = filename
    
    class MockPR:
        def __init__(self, files):
            self._files = files
        
        def get_files(self):
            return self._files
    
    # Test turquoise-squad
    print("\n✓ Test: turquoise-squad → turquoise")
    pr = MockPR([MockFile('tests/functional/disaster-recovery/test_dr.py')])
    squad = determine_squad(pr, None)
    assert squad == 'turquoise', f"Expected 'turquoise', got '{squad}'"
    print(f"  Result: {squad} ✅")
    
    # Test aqua-squad
    print("\n✓ Test: aqua-squad → aqua")
    pr = MockPR([MockFile('tests/lvmo/test_lvm.py')])
    squad = determine_squad(pr, None)
    assert squad == 'aqua', f"Expected 'aqua', got '{squad}'"
    print(f"  Result: {squad} ✅")
    
    # Test general (no match)
    print("\n✓ Test: No match → general")
    pr = MockPR([MockFile('README.md')])
    squad = determine_squad(pr, None)
    assert squad == 'general', f"Expected 'general', got '{squad}'"
    print(f"  Result: {squad} ✅")
    
    print("\n✅ All squad name tests passed!")


def test_search_logic():
    """Test PR number search logic"""
    print("\n" + "="*60)
    print("TEST 3: PR Number Search Logic")
    print("="*60)
    
    # Simulate the JavaScript search logic in Python
    def search_matches(pr_number, search_term):
        """Simulate JavaScript search logic"""
        pr_num_clean = pr_number.lower().replace('#', '')
        search_clean = search_term.lower().replace('#', '')
        return search_clean in pr_num_clean
    
    # Test cases
    test_cases = [
        ("#14712", "14712", True, "Search '14712' finds #14712"),
        ("#14712", "#14712", True, "Search '#14712' finds #14712"),
        ("#14712", "147", True, "Search '147' finds #14712"),
        ("#14712", "1471", True, "Search '1471' finds #14712"),
        ("#14712", "99999", False, "Search '99999' doesn't find #14712"),
        ("#1234", "234", True, "Partial match works"),
    ]
    
    for pr_num, search, expected, description in test_cases:
        result = search_matches(pr_num, search)
        status = "✅" if result == expected else "❌"
        print(f"\n✓ {description}")
        print(f"  PR: {pr_num}, Search: '{search}' → {result} {status}")
        assert result == expected, f"Test failed: {description}"
    
    print("\n✅ All search logic tests passed!")


def test_filter_combinations():
    """Test filter combination logic"""
    print("\n" + "="*60)
    print("TEST 4: Filter Combinations")
    print("="*60)
    
    # Mock PR data
    prs = [
        {'number': 1, 'squad': 'turquoise', 'status': 'waiting_reviewer', 'size': 'M', 'stale': False},
        {'number': 2, 'squad': 'aqua', 'status': 'waiting_author', 'size': 'L', 'stale': True},
        {'number': 3, 'squad': 'turquoise', 'status': 'approved', 'size': 'S', 'stale': False},
        {'number': 4, 'squad': 'brown', 'status': 'waiting_reviewer', 'size': 'XL', 'stale': True},
    ]
    
    def apply_filters(prs, squad=None, status=None, size=None, stale_only=False):
        """Apply filters to PR list"""
        filtered = prs
        if squad:
            filtered = [pr for pr in filtered if pr['squad'] == squad]
        if status:
            filtered = [pr for pr in filtered if pr['status'] == status]
        if size:
            filtered = [pr for pr in filtered if pr['size'] == size]
        if stale_only:
            filtered = [pr for pr in filtered if pr['stale']]
        return filtered
    
    # Test combinations
    print("\n✓ Test: Squad filter only")
    result = apply_filters(prs, squad='turquoise')
    assert len(result) == 2, f"Expected 2 PRs, got {len(result)}"
    print(f"  Filtered to {len(result)} PRs ✅")
    
    print("\n✓ Test: Status filter only")
    result = apply_filters(prs, status='waiting_reviewer')
    assert len(result) == 2, f"Expected 2 PRs, got {len(result)}"
    print(f"  Filtered to {len(result)} PRs ✅")
    
    print("\n✓ Test: Squad + Status")
    result = apply_filters(prs, squad='turquoise', status='waiting_reviewer')
    assert len(result) == 1, f"Expected 1 PR, got {len(result)}"
    print(f"  Filtered to {len(result)} PR ✅")
    
    print("\n✓ Test: Stale only")
    result = apply_filters(prs, stale_only=True)
    assert len(result) == 2, f"Expected 2 PRs, got {len(result)}"
    print(f"  Filtered to {len(result)} PRs ✅")
    
    print("\n✓ Test: Squad + Status + Size")
    result = apply_filters(prs, squad='turquoise', status='approved', size='S')
    assert len(result) == 1, f"Expected 1 PR, got {len(result)}"
    print(f"  Filtered to {len(result)} PR ✅")
    
    print("\n✅ All filter combination tests passed!")


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("🧪 OCS-CI PR PULSE - VALIDATION SUITE")
    print("="*60)
    
    try:
        test_pr_status_logic()
        test_squad_name_cleanup()
        test_search_logic()
        test_filter_combinations()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        print("\nThe dashboard logic is working correctly.")
        print("You can now regenerate the dashboard with: ./test_dashboard.sh")
        print("="*60 + "\n")
        
        return 0
    
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())

