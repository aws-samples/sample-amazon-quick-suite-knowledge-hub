const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, PutCommand } = require('@aws-sdk/lib-dynamodb');

const client = new DynamoDBClient({});
const dynamodb = DynamoDBDocumentClient.from(client);
const TABLE_NAME = process.env.METRICS_TABLE_NAME;

exports.handler = async (event) => {
  try {
    const items = buildFinanceData();
    for (const item of items) {
      await dynamodb.send(new PutCommand({ TableName: TABLE_NAME, Item: item }));
    }
    return { statusCode: 200, body: JSON.stringify({ message: 'Data seeded', count: items.length }) };
  } catch (error) {
    console.error('Error seeding data:', error);
    return { statusCode: 500, body: JSON.stringify({ error: 'Failed to seed data' }) };
  }
};

function buildFinanceData() {
  const items = [];

  // ── Monthly global metrics (Jan 2024 – Jun 2024) ──────────────────────────
  const months = [
    { id: 'month-2024-01', month: 'Jan', year: 2024, quarter: 'Q1', revenue: 1820000, expenses: 1430000 },
    { id: 'month-2024-02', month: 'Feb', year: 2024, quarter: 'Q1', revenue: 1950000, expenses: 1510000 },
    { id: 'month-2024-03', month: 'Mar', year: 2024, quarter: 'Q1', revenue: 2100000, expenses: 1620000 },
    { id: 'month-2024-04', month: 'Apr', year: 2024, quarter: 'Q2', revenue: 2250000, expenses: 1680000 },
    { id: 'month-2024-05', month: 'May', year: 2024, quarter: 'Q2', revenue: 2350000, expenses: 1720000 },
    { id: 'month-2024-06', month: 'Jun', year: 2024, quarter: 'Q2', revenue: 2450000, expenses: 1790000 },
    { id: 'month-2024-07', month: 'Jul', year: 2024, quarter: 'Q3', revenue: 2380000, expenses: 1850000 },
    { id: 'month-2024-08', month: 'Aug', year: 2024, quarter: 'Q3', revenue: 2200000, expenses: 1900000 },
    { id: 'month-2024-09', month: 'Sep', year: 2024, quarter: 'Q3', revenue: 2020000, expenses: 1880000 },
    { id: 'month-2024-10', month: 'Oct', year: 2024, quarter: 'Q4', revenue: 2310000, expenses: 1760000 },
    { id: 'month-2024-11', month: 'Nov', year: 2024, quarter: 'Q4', revenue: 2480000, expenses: 1810000 },
    { id: 'month-2024-12', month: 'Dec', year: 2024, quarter: 'Q4', revenue: 2650000, expenses: 1870000 },
    { id: 'month-2025-01', month: 'Jan', year: 2025, quarter: 'Q1', revenue: 2520000, expenses: 1920000 },
    { id: 'month-2025-02', month: 'Feb', year: 2025, quarter: 'Q1', revenue: 2610000, expenses: 1950000 },
    { id: 'month-2025-03', month: 'Mar', year: 2025, quarter: 'Q1', revenue: 2780000, expenses: 2010000 },
    { id: 'month-2025-04', month: 'Apr', year: 2025, quarter: 'Q2', revenue: 2890000, expenses: 2080000 },
    { id: 'month-2025-05', month: 'May', year: 2025, quarter: 'Q2', revenue: 2950000, expenses: 2120000 },
    { id: 'month-2025-06', month: 'Jun', year: 2025, quarter: 'Q2', revenue: 3050000, expenses: 2180000 },
  ];

  for (const m of months) {
    const profit = m.revenue - m.expenses;
    const margin = parseFloat(((profit / m.revenue) * 100).toFixed(2));
    const ts = new Date(`${m.year}-${String(months.indexOf(m) % 12 + 1).padStart(2,'0')}-01`).getTime();
    items.push({ ...m, type: 'monthly', timestamp: ts, profit, margin });
  }

  // ── Regional breakdown ────────────────────────────────────────────────────
  const regions = [
    // region, quarter, year, revenue, expenses, revenueGrowthPct
    { region: 'North America', quarter: 'Q1', year: 2024, revenue: 2850000, expenses: 2100000, growth: 8.2 },
    { region: 'North America', quarter: 'Q2', year: 2024, revenue: 3100000, expenses: 2250000, growth: 8.8 },
    { region: 'North America', quarter: 'Q3', year: 2024, revenue: 2980000, expenses: 2300000, growth: -3.9 },
    { region: 'North America', quarter: 'Q4', year: 2024, revenue: 3350000, expenses: 2280000, growth: 12.4 },
    { region: 'EMEA',          quarter: 'Q1', year: 2024, revenue: 1620000, expenses: 1280000, growth: 14.1 },
    { region: 'EMEA',          quarter: 'Q2', year: 2024, revenue: 1750000, expenses: 1340000, growth: 8.0 },
    { region: 'EMEA',          quarter: 'Q3', year: 2024, revenue: 1580000, expenses: 1350000, growth: -9.7 },
    { region: 'EMEA',          quarter: 'Q4', year: 2024, revenue: 1820000, expenses: 1310000, growth: 15.2 },
    { region: 'APAC',          quarter: 'Q1', year: 2024, revenue: 980000,  expenses: 780000,  growth: 22.5 },
    { region: 'APAC',          quarter: 'Q2', year: 2024, revenue: 1100000, expenses: 840000,  growth: 12.2 },
    { region: 'APAC',          quarter: 'Q3', year: 2024, revenue: 1040000, expenses: 870000,  growth: -5.5 },
    { region: 'APAC',          quarter: 'Q4', year: 2024, revenue: 1270000, expenses: 850000,  growth: 22.1 },
    { region: 'LATAM',         quarter: 'Q1', year: 2024, revenue: 420000,  expenses: 360000,  growth: 5.0 },
    { region: 'LATAM',         quarter: 'Q2', year: 2024, revenue: 450000,  expenses: 370000,  growth: 7.1 },
    { region: 'LATAM',         quarter: 'Q3', year: 2024, revenue: 400000,  expenses: 380000,  growth: -11.1 },
    { region: 'LATAM',         quarter: 'Q4', year: 2024, revenue: 470000,  expenses: 360000,  growth: 17.5 },
  ];

  regions.forEach((r, i) => {
    const profit = r.revenue - r.expenses;
    const margin = parseFloat(((profit / r.revenue) * 100).toFixed(2));
    items.push({ id: `region-${i}`, type: 'regional', timestamp: Date.now() - i * 1000, ...r, profit, margin });
  });

  // ── Product line breakdown ────────────────────────────────────────────────
  const products = [
    { product: 'Enterprise Software', quarter: 'Q1', year: 2024, revenue: 2100000, expenses: 1400000, margin: 33.3 },
    { product: 'Enterprise Software', quarter: 'Q2', year: 2024, revenue: 2280000, expenses: 1490000, margin: 34.6 },
    { product: 'Enterprise Software', quarter: 'Q3', year: 2024, revenue: 2050000, expenses: 1520000, margin: 25.9 },
    { product: 'Enterprise Software', quarter: 'Q4', year: 2024, revenue: 2450000, expenses: 1510000, margin: 38.4 },
    { product: 'Professional Services', quarter: 'Q1', year: 2024, revenue: 1200000, expenses: 980000, margin: 18.3 },
    { product: 'Professional Services', quarter: 'Q2', year: 2024, revenue: 1350000, expenses: 1050000, margin: 22.2 },
    { product: 'Professional Services', quarter: 'Q3', year: 2024, revenue: 1180000, expenses: 1020000, margin: 13.6 },
    { product: 'Professional Services', quarter: 'Q4', year: 2024, revenue: 1420000, expenses: 1040000, margin: 26.8 },
    { product: 'Cloud Subscriptions',  quarter: 'Q1', year: 2024, revenue: 680000,  expenses: 310000,  margin: 54.4 },
    { product: 'Cloud Subscriptions',  quarter: 'Q2', year: 2024, revenue: 750000,  expenses: 330000,  margin: 56.0 },
    { product: 'Cloud Subscriptions',  quarter: 'Q3', year: 2024, revenue: 720000,  expenses: 360000,  margin: 50.0 },
    { product: 'Cloud Subscriptions',  quarter: 'Q4', year: 2024, revenue: 840000,  expenses: 350000,  margin: 58.3 },
    { product: 'Hardware',             quarter: 'Q1', year: 2024, revenue: 390000,  expenses: 340000,  margin: 12.8 },
    { product: 'Hardware',             quarter: 'Q2', year: 2024, revenue: 420000,  expenses: 360000,  margin: 14.3 },
    { product: 'Hardware',             quarter: 'Q3', year: 2024, revenue: 350000,  expenses: 330000,  margin: 5.7  },
    { product: 'Hardware',             quarter: 'Q4', year: 2024, revenue: 400000,  expenses: 340000,  margin: 15.0 },
  ];

  products.forEach((p, i) => {
    const profit = p.revenue - p.expenses;
    items.push({ id: `product-${i}`, type: 'product', timestamp: Date.now() - i * 1000, ...p, profit });
  });

  // ── Department expense breakdown ──────────────────────────────────────────
  const departments = [
    { dept: 'Engineering',  quarter: 'Q3', year: 2024, budget: 1800000, actual: 1950000, variance: -150000, category: 'Salaries' },
    { dept: 'Sales',        quarter: 'Q3', year: 2024, budget: 980000,  actual: 1120000, variance: -140000, category: 'Salaries' },
    { dept: 'Marketing',    quarter: 'Q3', year: 2024, budget: 620000,  actual: 710000,  variance: -90000,  category: 'Marketing' },
    { dept: 'Operations',   quarter: 'Q3', year: 2024, budget: 540000,  actual: 580000,  variance: -40000,  category: 'Operations' },
    { dept: 'G&A',          quarter: 'Q3', year: 2024, budget: 380000,  actual: 420000,  variance: -40000,  category: 'G&A' },
    { dept: 'R&D',          quarter: 'Q3', year: 2024, budget: 450000,  actual: 480000,  variance: -30000,  category: 'R&D' },
    { dept: 'Engineering',  quarter: 'Q4', year: 2024, budget: 1800000, actual: 1780000, variance: 20000,   category: 'Salaries' },
    { dept: 'Sales',        quarter: 'Q4', year: 2024, budget: 980000,  actual: 960000,  variance: 20000,   category: 'Salaries' },
    { dept: 'Marketing',    quarter: 'Q4', year: 2024, budget: 620000,  actual: 640000,  variance: -20000,  category: 'Marketing' },
    { dept: 'Operations',   quarter: 'Q4', year: 2024, budget: 540000,  actual: 520000,  variance: 20000,   category: 'Operations' },
    { dept: 'G&A',          quarter: 'Q4', year: 2024, budget: 380000,  actual: 390000,  variance: -10000,  category: 'G&A' },
    { dept: 'R&D',          quarter: 'Q4', year: 2024, budget: 450000,  actual: 460000,  variance: -10000,  category: 'R&D' },
  ];

  departments.forEach((d, i) => {
    items.push({ id: `dept-${i}`, type: 'department', timestamp: Date.now() - i * 1000, ...d });
  });

  // ── Country-level revenue (for "which country had highest growth") ─────────
  const countries = [
    { country: 'United States', region: 'North America', revenue2024Q3: 2650000, revenue2024Q2: 2780000, growth: -4.7 },
    { country: 'United Kingdom', region: 'EMEA',         revenue2024Q3: 520000,  revenue2024Q2: 580000,  growth: -10.3 },
    { country: 'Germany',        region: 'EMEA',         revenue2024Q3: 480000,  revenue2024Q2: 510000,  growth: -5.9 },
    { country: 'France',         region: 'EMEA',         revenue2024Q3: 320000,  revenue2024Q2: 340000,  growth: -5.9 },
    { country: 'Japan',          region: 'APAC',         revenue2024Q3: 380000,  revenue2024Q2: 400000,  growth: -5.0 },
    { country: 'Australia',      region: 'APAC',         revenue2024Q3: 290000,  revenue2024Q2: 270000,  growth: 7.4  },
    { country: 'India',          region: 'APAC',         revenue2024Q3: 240000,  revenue2024Q2: 210000,  growth: 14.3 },
    { country: 'Singapore',      region: 'APAC',         revenue2024Q3: 130000,  revenue2024Q2: 110000,  growth: 18.2 },
    { country: 'Brazil',         region: 'LATAM',        revenue2024Q3: 240000,  revenue2024Q2: 270000,  growth: -11.1 },
    { country: 'Mexico',         region: 'LATAM',        revenue2024Q3: 160000,  revenue2024Q2: 180000,  growth: -11.1 },
    { country: 'Canada',         region: 'North America',revenue2024Q3: 330000,  revenue2024Q2: 310000,  growth: 6.5  },
  ];

  countries.forEach((c, i) => {
    items.push({ id: `country-${i}`, type: 'country', timestamp: Date.now() - i * 1000, ...c });
  });

  return items;
}
