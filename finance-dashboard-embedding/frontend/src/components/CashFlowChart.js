import React from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

const CashFlowChart = ({ data }) => {
  const chartData = {
    labels: data?.labels || [],
    datasets: [
      {
        label: 'Operating Cash Flow',
        data: data?.operating || [],
        borderColor: 'rgb(59, 130, 246)',
        backgroundColor: 'rgba(59, 130, 246, 0.1)',
        tension: 0.4,
        borderWidth: 3
      },
      {
        label: 'Investing Cash Flow',
        data: data?.investing || [],
        borderColor: 'rgb(168, 85, 247)',
        backgroundColor: 'rgba(168, 85, 247, 0.1)',
        tension: 0.4,
        borderWidth: 3
      },
      {
        label: 'Financing Cash Flow',
        data: data?.financing || [],
        borderColor: 'rgb(234, 179, 8)',
        backgroundColor: 'rgba(234, 179, 8, 0.1)',
        tension: 0.4,
        borderWidth: 3
      }
    ]
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top',
        labels: {
          font: { size: 12, weight: '600' },
          padding: 12
        }
      },
      title: {
        display: true,
        text: 'Cash Flow Analysis',
        font: { size: 16, weight: '700' },
        padding: 20,
        color: '#1e3a8a'
      }
    },
    scales: {
      y: {
        ticks: {
          callback: function(value) {
            return '$' + (value / 1000) + 'K';
          }
        }
      }
    }
  };

  return <Line data={chartData} options={options} />;
};

export default CashFlowChart;
