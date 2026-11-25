# Identity, Traits & Targeting Segments (Phase 7)

## Provisioned Trait-Based Segments in Flagsmith Project `#56055`
- `rg_internal_testers`: Matches `is_internal_tester == "true"` OR `user_type == "internal"`.
- `rg_qa_and_beta`: Matches `user_type == "qa"` OR `subscription_plan == "beta"`.
- `rg_returning_enterprise`: Matches `subscription_plan == "enterprise"`.

## Supported Non-Sensitive Targeting Traits
- `user_type` (`consumer`, `internal`, `qa`, `beta`)
- `subscription_plan` (`free`, `pro`, `beta`, `enterprise`)
- `app_version`, `is_internal_tester`, `organization_id`

## Verified Identity Explainer (`POST /api/v1/flagsmith/identities/explain`)
- Evaluates `shopper_42` against live Flagsmith Edge `POST /identities/` twice and verifies `consistent_on_repeat_evaluation: true` across all `10` flags (`IDENTITY_FLAGS: 10, CONSISTENT: True`).
- Returns exact Flagsmith Edge `reason` (`DEFAULT`, `SPLIT`, or `TARGETING_MATCH; segment=...`), `variant`, and `enabled` state.
- **Security Guarantee:** Traits are used solely for product targeting and never grant RBAC roles, bypass `PolicyEngine`, or authorize mutations.
