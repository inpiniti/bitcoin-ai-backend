/**
 * 예측 결과 포맷팅 모듈
 */

export interface ForecastItem {
    ds: string;
    y: number;
    timesfm?: number;
}

export interface ForecastReport {
    title: string;
    symbol: string;
    generatedAt: string;
    model: string;
    dataPoints: number;
    predictionCount: number;
    predictions: Array<{
        step: number;
        date: string;
        price: number;
        priceFormatted: string;
    }>;
}

export function formatForecastReport(
    symbol: string,
    model: string,
    dataPoints: number,
    forecast: ForecastItem[]
): ForecastReport {
    return {
        title: `${symbol} 가격 예측 보고서`,
        symbol,
        generatedAt: new Date().toISOString(),
        model,
        dataPoints,
        predictionCount: forecast.length,
        predictions: forecast.slice(0, 24).map((item, index) => ({
            step: index + 1,
            date: item.ds,
            price: Math.round(item.timesfm || item.y || 0),
            priceFormatted: new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'USD'
            }).format(item.timesfm || item.y || 0)
        }))
    };
}
