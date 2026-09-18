import React from 'react';
import { Bar } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

const QuarterlyComparisonChart = ({ data }) => {
  const chartData = {
    labels: data?.quarters || ['Q1', 'Q2', 'Q3', 'Q4'],
    datasets: [
      {
        label: 'Current Year',
        data: data?.currentYear || [],
        backgroundColor: 'rgba(30, 58, 138, 0.8)',
        borderColor: 'rgb(30, 58, 138)',
        borderWidth: 2
      },
      {
        label: 'Previous Year',
        data: data?.previousYear || [],
        backgroundColor: 'rgba(148, 163, 184, 0.8)',
        borderColor: 'rgb(148, 163, 184)',
        borderWidth: 2
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
          font: { size: 13, weight: '600' },
          padding: 15
        }
      },
      title: {
        display: true,
        text: 'Quarterly Revenue Comparison',
        font: { size: 16, weight: '700' },
        padding: 20,
        color: '#1e3a8a'
      }
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: {
          callback: function(value) {
            return '$' + (value / 1000) + 'K';
          }
        }
      }
    }
  };

  return <Bar data={chartData} options={options} />;
};

export default QuarterlyComparisonChart;
