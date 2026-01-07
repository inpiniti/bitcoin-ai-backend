export const config = {
    name: "format-forecast-result",
    type: "event",
    subscribes: ["format-forecast-result"],
    flows: ['bitcoin-forecast-flow'],
    emits: [],
};

export const handler = async (input: any, { logger }: any) => {
    const { forecast, symbol } = input;
    logger.info(`[Format] Generating report for ${symbol}...`);

    if (!Array.isArray(forecast)) {
        throw new Error("Invalid forecast data: expected array");
    }

    // 사람이 보기 좋게 포맷팅
    const formatted = {
        title: `${symbol} 가격 예측 보고서`,
        generatedAt: new Date().toISOString(),
        predictionCount: forecast.length,
        predictions: forecast.map((item: any, index: number) => {
            return {
                step: index + 1,
                date: item.ds,
                price: Math.round(item.y), // 소수점 반올림
                priceFormatted: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(item.y)
            };
        })
    };

    logger.info("[Format] Report generation completed.");
    return formatted;
};

