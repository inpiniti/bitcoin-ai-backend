/**
 * Step 4: Whale Result Formatting
 * Event Step - 'format-whale-result' 이벤트 구독
 */
export const config = {
    name: "format-whale-result",
    type: "event",
    subscribes: ['format-whale-result'],
    emits: [],
    flows: ['whale-tracking-flow']
};

export const handler = async (input: any, { state, logger }: any) => {
    const { jobId, symbol, analysis } = input;

    try {
        logger.info(`[Step4:FormatWhale] Formatting result for job ${jobId}`);

        // 분석 결과 해석 및 메시지 생성
        const { currentPrice, vwapShort, vwapDiffPercent, divergence, mfi, volumeSpike } = analysis;

        let sentiment = "neutral";
        let message = "";
        let signals = [];

        // 1. VWAP 분석
        if (vwapDiffPercent < -5) {
            signals.push(`🐋 세력 추정 평단가($${Math.round(vwapShort)})보다 5% 이상 저렴합니다.`);
            sentiment = "bullish";
        } else if (vwapDiffPercent > 10) {
            signals.push(`⚠️ 세력 평단가($${Math.round(vwapShort)})보다 10% 이상 비쌉니다. 차익 실현 주의.`);
            sentiment = "bearish";
        } else {
            signals.push(`📊 세력 평단가($${Math.round(vwapShort)})와 비슷한 수준입니다.`);
        }

        // 2. Divergence 분석 (강력한 신호)
        if (divergence === "bullish_divergence") {
            signals.push("🔥 [강력 매수 신호] 가격은 하락 중이나 자금(OBV)은 유입되고 있습니다 (개미 털기 의심).");
            sentiment = "strong_bullish";
        } else if (divergence === "bearish_divergence") {
            signals.push("🚨 [위험 신호] 가격은 버티고 있으나 자금(OBV)이 조용히 빠져나가고 있습니다.");
            sentiment = "strong_bearish";
        }

        // 3. MFI & Volume Spike
        if (mfi > 80) signals.push("📈 과매수 구간입니다 (MFI > 80).");
        if (mfi < 20) signals.push("📉 과매도 구간입니다 (MFI < 20).");
        if (volumeSpike) signals.push("💥 최근 거래량이 급증했습니다. 변동성 확대 주의.");

        const report = {
            title: `${symbol} 고래 수급 분석 리포트`,
            symbol,
            generatedAt: new Date().toISOString(),
            sentiment, // bullish, bearish, neutral, strong_bullish, strong_bearish
            currentPrice,
            estimatedWhalePrice: vwapShort,
            summary: signals.join("\n"),
            details: analysis
        };

        // State에 완료된 결과 저장
        const job = await state.get('whale_jobs', jobId);
        if (job) {
            job.status = 'completed';
            job.completedAt = new Date().toISOString();
            job.result = report;
            await state.set('whale_jobs', jobId, job);
        }

        logger.info(`[Step4:FormatWhale] Job ${jobId} completed successfully`);

    } catch (error: any) {
        logger.error(`[Step4:FormatWhale] Error for job ${jobId}: ${error.message}`);

        const job = await state.get('whale_jobs', jobId);
        if (job) {
            job.status = 'error';
            job.error = error.message;
            await state.set('whale_jobs', jobId, job);
        }
    }
};
