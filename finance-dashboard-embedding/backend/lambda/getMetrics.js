const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, ScanCommand } = require('@aws-sdk/lib-dynamodb');

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.METRICS_TABLE_NAME;

exports.handler = async (event) => {
  try {
    const result = await dynamodb.send(new ScanCommand({ TableName: TABLE_NAME, Limit: 10 }));
    const metrics = calculateMetrics(result.Items);
    const chartData = prepareChartData(result.Items);

    return {
      statusCode: 200,
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'GET,OPTIONS'
      },
      body: JSON.stringify({ metrics, chartData })
    };
  } catch (error) {
    console.error('Error:', error);
    return { statusCode: 500, body: JSON.stringify({ error: 'Internal server error' }) };
  }
};

function calculateMetrics(items) {
  const sorted = items.sort((a, b) => b.timestamp - a.timestamp);
  const latest = sorted[0] || {};
  const previous = sorted[1] || {};

  return {
    revenue: latest.revenue || 0,
    revenueTrend: calculateTrend(latest.revenue, previous.revenue),
    expenses: latest.expenses || 0,
    expensesTrend: calculateTrend(latest.expenses, previous.expenses),
    profit: (latest.revenue || 0) - (latest.expenses || 0),
    profitTrend: calculateTrend(
      (latest.revenue || 0) - (latest.expenses || 0),
      (previous.revenue || 0) - (previous.expenses || 0)
    ),
    margin: latest.revenue ? ((latest.revenue - latest.expenses) / latest.revenue * 100) : 0,
    marginTrend: 0
  };
}

function calculateTrend(current, previous) {
  if (!previous) return 0;
  return ((current - previous) / previous) * 100;
}

function prepareChartData(items) {
  const sorted = items.sort((a, b) => a.timestamp - b.timestamp);
  return {
    labels: sorted.map(item => new Date(item.timestamp).toLocaleDateString('en-US', { month: 'short' })),
    revenue: sorted.map(item => item.revenue || 0),
    expenses: sorted.map(item => item.expenses || 0),
    margins: sorted.map(item => {
      const rev = item.revenue || 0;
      const exp = item.expenses || 0;
      return rev ? parseFloat(((rev - exp) / rev * 100).toFixed(1)) : 0;
    }),
    operating: sorted.map(item => (item.revenue || 0) * 0.28),
    investing: sorted.map(item => (item.revenue || 0) * -0.08),
    financing: sorted.map(item => (item.revenue || 0) * 0.03),
    quarters: ['Q1', 'Q2', 'Q3', 'Q4'],
    currentYear: [585000, 675000, 720000, 780000],
    previousYear: [520000, 610000, 650000, 690000],
    categories: ['Salaries', 'Operations', 'Marketing', 'R&D', 'Other'],
    values: [45, 25, 15, 10, 5]
  };
}
