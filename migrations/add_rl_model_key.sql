-- automation_settings 테이블에 RL 모델 키 컬럼 추가 (선택 사항)
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'automation_settings'
          and column_name = 'rl_model_key'
    ) then
        alter table automation_settings add column rl_model_key uuid;
    end if;
end $$;

-- top_tickers_log 테이블에 RL 모델 키 컬럼 추가 (예측에 사용된 RL 모델 기록)
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'top_tickers_log'
          and column_name = 'rl_model_key'
    ) then
        alter table top_tickers_log add column rl_model_key uuid;
    end if;
end $$;
