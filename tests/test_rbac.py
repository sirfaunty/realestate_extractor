"""
End-to-end RBAC permission verification test.

Tests:
1. Admin gets full access to everything
2. Analyst gets read-only on property data, no extraction/admin
3. Viewer gets read-only property data, no extraction write, review read
4. Custom overrides work correctly
5. Permission checking helpers enforce level hierarchy
"""

import sys
import os
import json
import tempfile

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from permissions import (
    PermissionStore, ROLE_TEMPLATES, SCOPES, SCOPE_ORDER, LEVELS,
    check_permission, can_read, can_edit, get_scope_categories
)

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}")


# ─── Setup ──────────────────────────────────────────────────────────

print("\n═══ RBAC Permission Verification ═══\n")

# Use temp database
db_path = tempfile.mktemp(suffix='.db')
store = PermissionStore(db_path)
store.connect()

ORG = 'test-org-001'

# ─── 1. Role Templates ──────────────────────────────────────────────

print("1. Role Template Definitions")
test("Admin template has edit on all scopes",
     all(v == 'edit' for v in ROLE_TEMPLATES['admin']['permissions'].values()))

test("Analyst has no extraction access",
     ROLE_TEMPLATES['analyst']['permissions']['extraction.upload'] == 'none' and
     ROLE_TEMPLATES['analyst']['permissions']['extraction.batch'] == 'none' and
     ROLE_TEMPLATES['analyst']['permissions']['extraction.review'] == 'none')

test("Analyst has read-only property access",
     all(ROLE_TEMPLATES['analyst']['permissions'][s] == 'read'
         for s in SCOPES if s.startswith('property.')))

test("Viewer has no admin access",
     ROLE_TEMPLATES['viewer']['permissions']['admin.users'] == 'none' and
     ROLE_TEMPLATES['viewer']['permissions']['admin.settings'] == 'none')

test("Operator can edit extraction scopes",
     ROLE_TEMPLATES['operator']['permissions']['extraction.upload'] == 'edit' and
     ROLE_TEMPLATES['operator']['permissions']['extraction.batch'] == 'edit' and
     ROLE_TEMPLATES['operator']['permissions']['extraction.review'] == 'edit')

test("All templates cover all scopes",
     all(len(tmpl['permissions']) == len(SCOPES) for tmpl in ROLE_TEMPLATES.values()))


# ─── 2. Admin User ──────────────────────────────────────────────────

print("\n2. Admin User — Full Access")
store.set_user_role('admin-user', ORG, 'admin')
admin_perms = store.get_user_permissions('admin-user', ORG)

test("Admin can read all scopes",
     all(can_read(admin_perms, s) for s in SCOPES))
test("Admin can edit all scopes",
     all(can_edit(admin_perms, s) for s in SCOPES))
test("Admin role stored correctly",
     store.get_user_role('admin-user', ORG) == 'admin')


# ─── 3. Analyst User — Read-Only Property ───────────────────────────

print("\n3. Analyst User — Read-Only Property Data")
store.set_user_role('analyst-user', ORG, 'analyst')
analyst_perms = store.get_user_permissions('analyst-user', ORG)

test("Analyst can read property.operations",
     can_read(analyst_perms, 'property.operations'))
test("Analyst cannot edit property.operations",
     not can_edit(analyst_perms, 'property.operations'))
test("Analyst can read property.debt",
     can_read(analyst_perms, 'property.debt'))
test("Analyst cannot edit property.debt",
     not can_edit(analyst_perms, 'property.debt'))
test("Analyst cannot read extraction.upload",
     not can_read(analyst_perms, 'extraction.upload'))
test("Analyst cannot read admin.users",
     not can_read(analyst_perms, 'admin.users'))


# ─── 4. Viewer User ─────────────────────────────────────────────────

print("\n4. Viewer User — Read-Only + Review Read")
store.set_user_role('viewer-user', ORG, 'viewer')
viewer_perms = store.get_user_permissions('viewer-user', ORG)

test("Viewer can read property.valuation",
     can_read(viewer_perms, 'property.valuation'))
test("Viewer cannot edit property.valuation",
     not can_edit(viewer_perms, 'property.valuation'))
test("Viewer can read extraction.review",
     can_read(viewer_perms, 'extraction.review'))
test("Viewer cannot edit extraction.review",
     not can_edit(viewer_perms, 'extraction.review'))
test("Viewer cannot read extraction.upload",
     not can_read(viewer_perms, 'extraction.upload'))


# ─── 5. Operator User ───────────────────────────────────────────────

print("\n5. Operator User — Property Edit + Extraction Edit")
store.set_user_role('operator-user', ORG, 'operator')
operator_perms = store.get_user_permissions('operator-user', ORG)

test("Operator can edit property.operations",
     can_edit(operator_perms, 'property.operations'))
test("Operator can read property.debt (not edit)",
     can_read(operator_perms, 'property.debt') and not can_edit(operator_perms, 'property.debt'))
test("Operator can edit extraction.upload",
     can_edit(operator_perms, 'extraction.upload'))
test("Operator cannot access admin.users",
     not can_read(operator_perms, 'admin.users'))


# ─── 6. Per-User Overrides ──────────────────────────────────────────

print("\n6. Per-User Overrides")

# Give analyst edit access to property.operations (override from read to edit)
store.set_user_override('analyst-user', ORG, 'property.operations', 'edit')
analyst_overridden = store.get_user_permissions('analyst-user', ORG)

test("Override: analyst now has edit on property.operations",
     can_edit(analyst_overridden, 'property.operations'))
test("Override: analyst still read-only on property.debt (unchanged)",
     can_read(analyst_overridden, 'property.debt') and
     not can_edit(analyst_overridden, 'property.debt'))

overrides = store.get_user_overrides('analyst-user', ORG)
test("Override stored correctly",
     overrides.get('property.operations') == 'edit')

# Setting override back to template default should remove it
store.set_user_override('analyst-user', ORG, 'property.operations', 'read')
overrides_after = store.get_user_overrides('analyst-user', ORG)
test("Override removed when matches template default",
     'property.operations' not in overrides_after)


# ─── 7. Bulk Overrides ──────────────────────────────────────────────

print("\n7. Bulk Overrides")
store.set_bulk_overrides('viewer-user', ORG, {
    'property.operations': 'edit',  # upgrade from read
    'extraction.upload': 'read',    # upgrade from none
    'property.debt': 'read',        # same as template — should NOT be stored
})
viewer_bulk = store.get_user_permissions('viewer-user', ORG)
viewer_overrides = store.get_user_overrides('viewer-user', ORG)

test("Bulk: viewer now has edit on property.operations",
     can_edit(viewer_bulk, 'property.operations'))
test("Bulk: viewer now has read on extraction.upload",
     can_read(viewer_bulk, 'extraction.upload'))
test("Bulk: property.debt NOT stored as override (matches template)",
     'property.debt' not in viewer_overrides)
test("Bulk: 2 overrides stored (not 3)",
     len(viewer_overrides) == 2)


# ─── 8. Role Change Clears Overrides ────────────────────────────────

print("\n8. Role Change Clears Overrides")
store.set_user_role('viewer-user', ORG, 'operator')
viewer_now_operator = store.get_user_overrides('viewer-user', ORG)
test("Role change clears all overrides",
     len(viewer_now_operator) == 0)
test("New role applied correctly",
     store.get_user_role('viewer-user', ORG) == 'operator')


# ─── 9. Permission Check Helpers ────────────────────────────────────

print("\n9. Permission Level Hierarchy")
test("check_permission: edit >= read",
     check_permission({'scope': 'edit'}, 'scope', 'read'))
test("check_permission: edit >= edit",
     check_permission({'scope': 'edit'}, 'scope', 'edit'))
test("check_permission: read >= read",
     check_permission({'scope': 'read'}, 'scope', 'read'))
test("check_permission: read < edit",
     not check_permission({'scope': 'read'}, 'scope', 'edit'))
test("check_permission: none < read",
     not check_permission({'scope': 'none'}, 'scope', 'read'))
test("check_permission: missing scope = none",
     not check_permission({}, 'missing', 'read'))


# ─── 10. Legacy Role Mapping ────────────────────────────────────────

print("\n10. Legacy Role Mapping (init_user_permissions)")
store.init_user_permissions('legacy-admin', ORG, role='admin')
test("Legacy 'admin' → admin template",
     store.get_user_role('legacy-admin', ORG) == 'admin')

store.init_user_permissions('legacy-member', ORG, role='member')
test("Legacy 'member' → operator template",
     store.get_user_role('legacy-member', ORG) == 'operator')

store.init_user_permissions('legacy-viewer', ORG, role='viewer')
test("Legacy 'viewer' → viewer template",
     store.get_user_role('legacy-viewer', ORG) == 'viewer')

store.init_user_permissions('legacy-unknown', ORG, role='something')
test("Legacy unknown role → viewer template",
     store.get_user_role('legacy-unknown', ORG) == 'viewer')


# ─── 11. Org-Level Listing ──────────────────────────────────────────

print("\n11. Org-Level Permission Listing")
all_perms = store.list_org_permissions(ORG)
test("list_org_permissions returns all users in org",
     len(all_perms) >= 4)  # admin, analyst, viewer/operator, legacy users

admin_entry = next((p for p in all_perms if p['user_id'] == 'admin-user'), None)
test("Listing includes resolved permissions",
     admin_entry and admin_entry['permissions']['property.operations'] == 'edit')


# ─── 12. Scope Categories (UI helper) ───────────────────────────────

print("\n12. Scope Categories for UI")
cats = get_scope_categories()
test("Three categories exist",
     len(cats) == 3)
test("Categories are: Property Data, Extraction, Admin",
     set(cats.keys()) == {'Property Data', 'Extraction', 'Admin'})
test("Property Data has 5 scopes",
     len(cats['Property Data']) == 5)
test("Each scope has key, label, description",
     all(all(k in s for k in ('key', 'label', 'description'))
         for cat_scopes in cats.values() for s in cat_scopes))


# ─── Cleanup ─────────────────────────────────────────────────────────

store.close()
os.unlink(db_path)


# ─── Results ─────────────────────────────────────────────────────────

print(f"\n{'═' * 50}")
print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
if failed == 0:
    print("All RBAC tests passed! ✓")
else:
    print(f"WARNING: {failed} test(s) failed!")
    sys.exit(1)
