# 🧪 시총 유추 API 로컬 테스트 스크립트
# 로컬 서버(http://localhost:7860)가 실행 중이어야 합니다.

param(
    [string]$Ticker = "AAPL"
)

Write-Host "🔍 [$Ticker] 시총 유추 API 테스트 시작..." -ForegroundColor Cyan

$body = @{
    symbol = $Ticker
} | ConvertTo-Json

try {
    $startTime = Get-Date
    $response = Invoke-RestMethod -Method POST -Uri "http://localhost:7860/v1/market-cap" `
        -ContentType "application/json" `
        -Body $body

    $endTime = Get-Date
    $duration = ($endTime - $startTime).TotalSeconds

    Write-Host "`n✨ 응답 결과 (소요 시간: $($duration.ToString('F2'))초):" -ForegroundColor Green
    Write-Host "----------------------------------------"
    Write-Host "심볼: $($response.symbol)"
    Write-Host "실제 시총: $($response.actual_market_cap.ToString('N0'))"
    Write-Host "유추 시총: $($response.inferred_market_cap.ToString('N0'))"
    Write-Host "오차금액: $($response.diff_value.ToString('N0'))"
    Write-Host "오차율: $($response.diff_percent.ToString('F2'))%"
    Write-Host "모델 손실: $($response.model_loss)"
    Write-Host "캐시 여부: $($response.cached) ($($response.cache_source))"
    Write-Host "----------------------------------------"

    if ([Math]::Abs($response.diff_percent) -gt 100) {
        Write-Host "⚠️ 경고: 오차율이 여전히 높습니다. 모델 추가 수정이 필요할 수 있습니다." -ForegroundColor Yellow
    }
    else {
        Write-Host "✅ 성공: 결과값이 현실적인 범위 내에 있습니다." -ForegroundColor Green
    }

}
catch {
    Write-Host "❌ 에러 발생: 서버가 실행 중인지 확인하세요 (npm run dev)." -ForegroundColor Red
    Write-Host $_.Exception.Message
}
