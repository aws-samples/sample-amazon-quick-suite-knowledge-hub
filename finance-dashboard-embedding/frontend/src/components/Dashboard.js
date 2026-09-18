import React from 'react';
import MetricsCard from './MetricsCard';
import RevenueChart from './RevenueChart';
import ProfitMarginChart from './ProfitMarginChart';
import CashFlowChart from './CashFlowChart';
import ExpenseBreakdownChart from './ExpenseBreakdownChart';
import QuarterlyComparisonChart from './QuarterlyComparisonChart';
import './Dashboard.css';

const Dashboard = ({ data }) => {
  if (!data) {
    return <div className="no-data">No data available</div>;
  }

  const { metrics, chartData } = data;

  return (
    <div className="dashboard">
      <div className="metrics-grid">
        <MetricsCard 
          title="Total Revenue" 
          value={metrics?.revenue || 0}
          trend={metrics?.revenueTrend || 0}
          format="currency"
        />
        <MetricsCard 
          title="Total Expenses" 
          value={metrics?.expenses || 0}
          trend={metrics?.expensesTrend || 0}
          format="currency"
        />
        <MetricsCard 
          title="Net Profit" 
          value={metrics?.profit || 0}
          trend={metrics?.profitTrend || 0}
          format="currency"
        />
        <MetricsCard 
          title="Profit Margin" 
          value={metrics?.margin || 0}
          trend={metrics?.marginTrend || 0}
          format="percentage"
        />
      </div>
      
      <div className="charts-section">
        <div className="chart-card full-width">
          <RevenueChart data={chartData} />
        </div>
      </div>

      <div className="charts-grid">
        <div className="chart-card">
          <ProfitMarginChart data={chartData} />
        </div>
        <div className="chart-card">
          <QuarterlyComparisonChart data={chartData} />
        </div>
      </div>

      <div className="charts-grid">
        <div className="chart-card">
          <CashFlowChart data={chartData} />
        </div>
        <div className="chart-card">
          <ExpenseBreakdownChart data={chartData} />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
