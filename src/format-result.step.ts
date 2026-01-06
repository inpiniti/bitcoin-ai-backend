export const config = {
    name: "format-forecast-result",
    type: "event",
    subscribes: ["format-forecast-result"],
    flows: ['bitcoin-forecast-flow'],
    emits: [],
};

export const handler = async (input: any) => {
    const { forecast, symbol, initialDate } = input;

    // forecast가 { forecast: [...] } 형태로 넘어올 수 있음 (Python Step 반환 구조 확인 필요)
    // 여기서는 Python Step이 { forecast: [{ds, y}, ... ] } 형태를 준다고 가정하거나
    // 단순 값 배열이라면 날짜를 생성해서 매핑

    if (!Array.isArray(forecast)) {
        throw new Error("Invalid forecast data: expected array");
    }

    // 사람이 보기 좋게 포맷팅
    const formatted = {
        title: `${symbol} 가격 예측 보고서`,
        generatedAt: new Date().toISOString(),
        predictionCount: forecast.length,
        predictions: forecast.map((item: any, index: number) => {
            // Python에서 이미 ds(날짜)를 만들어줬다면 그대로 사용
            return {
                step: index + 1,
                date: item.ds,
                price: Math.round(item.y), // 소수점 반올림
                priceFormatted: new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(item.y)
            };
        })
    };

    return formatted;
};
