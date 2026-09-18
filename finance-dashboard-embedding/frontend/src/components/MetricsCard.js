import React from 'react';
import './MetricsCard.css';

const MetricsCard = ({ title, value, trend, format }) => {
  const formatValue = (val) => {
    if (format === 'currency') {
      return `$${(val / 1000).toFixed(1)}K`;
    }
    if (format === 'percentage') {
      return `${val.toFixed(1)}%`;
    }
    return val.toLocaleString();
  };

  const trendClass = trend >= 0 ? 'positive' : 'negative';
  const trendIcon = trend >= 0 ? '↑' : '↓';

  return (
    <div className="metrics-card">
      <h3 className="card-title">{title}</h3>
      <div className="card-value">{formatValue(value)}</div>
      <div className={`card-trend ${trendClass}`}>
        <span className="trend-icon">{trendIcon}</span>
        <span>{Math.abs(trend).toFixed(1)}%</span>
      </div>
    </div>
  );
};

export default MetricsCard;
