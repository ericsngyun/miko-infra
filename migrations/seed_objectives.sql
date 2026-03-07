-- ============================================================================
-- seed_objectives.sql — Three sprint objectives loaded at first deploy
-- Run after all migrations on master-postgres
-- ============================================================================

INSERT INTO objectives (description, level, owner, success_condition, priority_weight, risk_weight, valid_until) VALUES
    ('Achieve Node 1 Go/No-Go — all 16 gates passing',
     'sprint_30d', 'eric',
     '16/16 gates logged in awaas_decisions',
     1.0, 0.3, NOW() + INTERVAL '30 days'),

    ('Book 20 Pleadly discovery calls in OC/LA market',
     'sprint_30d', 'david',
     '20 calls booked with PI firm decision makers',
     0.9, 0.2, NOW() + INTERVAL '30 days'),

    ('Close 2 paid Pleadly pilots',
     'quarterly', 'joint',
     '2 signed agreements, first deliverable sent and accepted',
     0.95, 0.3, NOW() + INTERVAL '90 days')
ON CONFLICT DO NOTHING;
