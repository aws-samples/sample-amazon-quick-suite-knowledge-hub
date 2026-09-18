import React from 'react';
import { Doughnut } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend
} from 'chart.js';

ChartJS.register(ArcElement, Tooltip, Legend);

const ExpenseBreakdownChart = ({ data }) => {
  const chartData = {
    labels: data?.categories || ['Salaries', 'Operations', 'Marketing', 'R&D', 'Other'],
    datasets: [
      {
        label: 'Expenses',
        data: data?.values || [45, 25, 15, 10, 5],
        backgroundColor: [
          'rgba(239, 68, 68, 0.8)',
          'rgba(59, 130, 246, 0.8)',
          'rgba(16, 185, 129, 0.8)',
          'rgba(168, 85, 247, 0.8)',
          'rgba(234, 179, 8, 0.8)'
        ],
        borderColor: [
          'rgb(239, 68, 68)',
          'rgb(59, 130, 246)',
          'rgb(16, 185, 129)',
          'rgb(168, 85, 247)',
          'rgb(234, 179, 8)'
        ],
        borderWidth: 2
      }
    ]
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'right',
        labels: {
          font: { size: 12, weight: '600' },
          padding: 15
        }
      },
      title: {
        display: true,
        text: 'Expense Breakdown',
        font: { size: 16, weight: '700' },
        padding: 20,
        color: '#1e3a8a'
      },
      tooltip: {
        callbacks: {
          label: function(context) {
            return context.label + ': ' + context.parsed + '%';
          }
        }
      }
    }
  };

  return <Doughnut data={chartData} options={options} />;
};

export default ExpenseBreakdownChart;
