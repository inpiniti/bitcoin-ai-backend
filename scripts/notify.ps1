# Bitcoin AI Backend - Agent Notification Script
# 에이전트 작업 완료 시 윈도우 알림을 표시합니다.

param(
    [Parameter(Mandatory=$true)]
    [string]$AgentName,
    
    [Parameter(Mandatory=$true)]
    [string]$Message,
    
    [Parameter(Mandatory=$false)]
    [ValidateSet("Info", "Warning", "Error", "Success")]
    [string]$Type = "Info"
)

# Windows Forms 어셈블리 로드
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# 아이콘 설정
$icon = switch ($Type) {
    "Success" { [System.Windows.Forms.MessageBoxIcon]::Information }
    "Warning" { [System.Windows.Forms.MessageBoxIcon]::Warning }
    "Error"   { [System.Windows.Forms.MessageBoxIcon]::Error }
    default   { [System.Windows.Forms.MessageBoxIcon]::Information }
}

# 이모지 접두사
$emoji = switch ($Type) {
    "Success" { "✅" }
    "Warning" { "⚠️" }
    "Error"   { "❌" }
    default   { "ℹ️" }
}

# 에이전트별 이모지
$agentEmoji = switch ($AgentName) {
    "Research"    { "🔬" }
    "Developer"   { "💻" }
    "Quality"     { "🧪" }
    "Infra"       { "🚀" }
    default       { "🤖" }
}

# 메시지 박스 표시
$title = "$agentEmoji $AgentName Agent"
$fullMessage = "$emoji $Message"

[System.Windows.Forms.MessageBox]::Show(
    $fullMessage,
    $title,
    [System.Windows.Forms.MessageBoxButtons]::OK,
    $icon
)

# 콘솔에도 출력
Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "$agentEmoji [$AgentName Agent] $emoji" -ForegroundColor Yellow
Write-Host "───────────────────────────────────────────" -ForegroundColor Cyan
Write-Host $Message -ForegroundColor White
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

<#
.SYNOPSIS
    에이전트 작업 완료 알림을 표시합니다.

.DESCRIPTION
    서브에이전트가 작업을 완료했을 때 사용자에게 OS 알림을 보냅니다.
    이를 통해 사용자는 다른 작업을 하다가 알림이 올 때만 개입할 수 있습니다.

.PARAMETER AgentName
    알림을 보내는 에이전트 이름 (Research, Developer, Quality, Infra)

.PARAMETER Message
    알림 메시지 내용

.PARAMETER Type
    알림 유형 (Info, Warning, Error, Success)

.EXAMPLE
    .\notify.ps1 -AgentName "Developer" -Message "새 API 개발 완료!" -Type "Success"

.EXAMPLE
    .\notify.ps1 -AgentName "Quality" -Message "테스트 실패: whale API 502 에러" -Type "Error"

.EXAMPLE
    .\notify.ps1 -AgentName "Research" -Message "Motia 문서 업데이트 발견" -Type "Info"
#>
