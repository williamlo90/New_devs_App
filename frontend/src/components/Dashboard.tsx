import React, { useEffect, useState } from "react";
import { RevenueSummary } from "./RevenueSummary";
import { SecureAPI } from "../lib/secureApi";
import { useAuth } from "../contexts/AuthContext.new";

interface Property { id: string; name: string; timezone: string }

const TenantDashboard: React.FC = () => {
  const [properties, setProperties] = useState<Property[]>([]);
  const [selectedProperty, setSelectedProperty] = useState('');
  const [period, setPeriod] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    SecureAPI.getDashboardProperties().then((result) => {
      if (!active) return;
      setProperties(result);
      setSelectedProperty(result[0]?.id || '');
    }).catch(() => {
      if (active) setError('Failed to load your properties');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, []);

  return (
    <div className="p-4 lg:p-6 min-h-full">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-gray-900">Property Management Dashboard</h1>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 lg:p-6">
          <div className="mb-6">
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-4">
              <div>
                <h2 className="text-lg lg:text-xl font-medium text-gray-900 mb-2">Revenue Overview</h2>
                <p className="text-sm lg:text-base text-gray-600">
                  {period ? `Check-in revenue for ${period}` : 'All-time check-in revenue'}
                  {selectedProperty && ` (${properties.find(p => p.id === selectedProperty)?.timezone})`}
                </p>
              </div>
              
              {/* Property Selector */}
              <div className="flex flex-col sm:items-end">
                <label htmlFor="dashboard-property" className="text-xs font-medium text-gray-700 mb-1">Select Property</label>
                <select
                  id="dashboard-property"
                  disabled={loading || properties.length === 0}
                  value={selectedProperty}
                  onChange={(e) => setSelectedProperty(e.target.value)}
                  className="block w-full sm:w-auto min-w-[200px] px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-blue-500 focus:border-blue-500 text-sm"
                >
                  {properties.map((property) => (
                    <option key={property.id} value={property.id}>
                      {property.name}
                    </option>
                  ))}
                </select>
                <label htmlFor="reporting-month" className="text-xs font-medium text-gray-700 mt-3 mb-1">Reporting month (empty = all time)</label>
                <input id="reporting-month" type="month" min="0001-01" max="9998-12" value={period}
                  onChange={e => setPeriod(e.target.value)} className="border border-gray-300 rounded-md px-3 py-2 text-sm" />
              </div>
            </div>
          </div>

          <div className="space-y-6">
            {loading && <p>Loading your properties...</p>}
            {error && <p role="alert">{error}</p>}
            {!loading && !error && properties.length === 0 && <p>No properties available.</p>}
            {selectedProperty && <RevenueSummary key={`${selectedProperty}:${period}`} propertyId={selectedProperty} period={period} />}
          </div>
        </div>
      </div>
    </div>
  );
};

const Dashboard: React.FC = () => {
  const { user } = useAuth();
  // A session switch must discard property selections and outstanding UI results.
  return user ? <TenantDashboard key={`${user.id}:${user.tenant_id}`} /> : null;
};

export default Dashboard;
