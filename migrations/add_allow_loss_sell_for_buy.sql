-- Add rebalance option for loss positions in automation settings
-- Run in Supabase SQL Editor

alter table if exists automation_settings
add column if not exists allow_loss_sell_for_buy boolean;

-- Default behavior:
-- - If prevent_loss_sell is false, this option is treated as enabled (auto-checked in UI)
-- - If prevent_loss_sell is true, keep explicit control (default false when null)
update automation_settings
set allow_loss_sell_for_buy = true
where (prevent_loss_sell is false or prevent_loss_sell is null)
  and allow_loss_sell_for_buy is null;

update automation_settings
set allow_loss_sell_for_buy = false
where prevent_loss_sell is true
  and allow_loss_sell_for_buy is null;
