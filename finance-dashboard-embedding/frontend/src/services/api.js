import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'https://your-api-gateway-url.amazonaws.com/prod';

export const fetchFinanceData = async () => {
  try {
    const response = await axios.get(`${API_BASE_URL}/metrics`);
    return response.data;
  } catch (error) {
    console.error('API Error:', error);
    // Return mock data for development
    return getMockData();
  }
};

const getMockData = () => ({
  metrics: {
    revenue: 245000,
    revenueTrend: 12.5,
    expenses: 180000,
    expensesTrend: -3.2,
    profit: 65000,
    profitTrend: 18.7,
    margin: 26.5,
    marginTrend: 4.2
  },
  chartData: {
    labels: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'],
    revenue: [180000, 195000, 210000, 225000, 235000, 245000],
    expenses: [150000, 155000, 165000, 170000, 175000, 180000],
    margins: [16.7, 20.5, 21.4, 24.4, 25.5, 26.5],
    operating: [45000, 52000, 58000, 62000, 65000, 70000],
    investing: [-15000, -18000, -12000, -20000, -16000, -14000],
    financing: [8000, 5000, 3000, 7000, 4000, 6000],
    quarters: ['Q1', 'Q2', 'Q3', 'Q4'],
    currentYear: [585000, 675000, 720000, 780000],
    previousYear: [520000, 610000, 650000, 690000],
    categories: ['Salaries', 'Operations', 'Marketing', 'R&D', 'Other'],
    values: [45, 25, 15, 10, 5]
  }
});
