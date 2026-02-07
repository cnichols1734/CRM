/**
 * Transaction Detail - RentCast Property Intelligence
 * Handles: property data fetching, rendering, format helpers
 */

// Store the current data for re-rendering
let currentRentCastData = TX_CONFIG.rentcastData;

// Format helpers
function formatCurrency(amount) {
    if (!amount && amount !== 0) return '—';
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(amount);
}

function formatNumber(num) {
    if (!num && num !== 0) return '—';
    return new Intl.NumberFormat('en-US').format(num);
}

function formatDate(dateStr) {
    if (!dateStr) return '—';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function calculateAppreciation(oldPrice, newPrice) {
    if (!oldPrice || !newPrice) return null;
    const change = ((newPrice - oldPrice) / oldPrice) * 100;
    const sign = change >= 0 ? '+' : '';
    return sign + change.toFixed(1) + '%';
}

function loadRentCastData() {
    // Show loading state
    document.getElementById('intel-initial-state').classList.add('hidden');
    document.getElementById('intel-error-state').classList.add('hidden');
    document.getElementById('intel-data-display').classList.add('hidden');
    document.getElementById('intel-loading-state').classList.remove('hidden');

    fetch(`/transactions/${transactionId}/rentcast-data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        document.getElementById('intel-loading-state').classList.add('hidden');

        if (data.success) {
            currentRentCastData = data.data;

            // Show message if cached
            if (data.cached && data.message) {
                showToast(data.message, 'info');
            }

            // Update last updated text
            if (data.fetched_at) {
                const fetchedDate = new Date(data.fetched_at);
                document.getElementById('intel-last-updated').textContent =
                    'Last updated: ' + fetchedDate.toLocaleDateString('en-US', {
                        month: 'long', day: 'numeric', year: 'numeric',
                        hour: 'numeric', minute: '2-digit'
                    });
            }

            // Render the data
            renderRentCastData(data.data);

            // Show data display and refresh button
            document.getElementById('intel-data-display').classList.remove('hidden');
            if (!document.getElementById('refresh-btn')) {
                // Add refresh button if it doesn't exist
                const headerDiv = document.querySelector('#content-intelligence .premium-card > .flex');
                const refreshBtn = document.createElement('button');
                refreshBtn.id = 'refresh-btn';
                refreshBtn.className = 'text-sm px-4 py-2 bg-slate-100 text-slate-700 rounded-xl hover:bg-slate-200 transition-colors font-medium';
                refreshBtn.innerHTML = '<i class="fas fa-sync-alt mr-2"></i>Refresh';
                refreshBtn.onclick = refreshRentCastData;
                headerDiv.appendChild(refreshBtn);
            }
        } else {
            document.getElementById('intel-error-message').textContent = data.error || 'An error occurred.';
            document.getElementById('intel-error-state').classList.remove('hidden');
        }
    })
    .catch(err => {
        document.getElementById('intel-loading-state').classList.add('hidden');
        document.getElementById('intel-error-message').textContent = 'Network error. Please try again.';
        document.getElementById('intel-error-state').classList.remove('hidden');
    });
}

function refreshRentCastData() {
    // Show spinner on button
    const btn = document.getElementById('refresh-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Refreshing...';
    btn.disabled = true;

    // Simulate short delay for UX (2-3 seconds)
    const minDelay = 2000;
    const startTime = Date.now();

    fetch(`/transactions/${transactionId}/rentcast-data`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        // Ensure minimum delay for UX
        const elapsed = Date.now() - startTime;
        const remainingDelay = Math.max(0, minDelay - elapsed);

        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;

            if (data.success) {
                currentRentCastData = data.data;

                // Show message
                if (data.cached && data.message) {
                    showToast(data.message, 'info');
                } else {
                    showToast('Property data refreshed!', 'success');
                }

                // Update last updated text
                if (data.fetched_at) {
                    const fetchedDate = new Date(data.fetched_at);
                    document.getElementById('intel-last-updated').textContent =
                        'Last updated: ' + fetchedDate.toLocaleDateString('en-US', {
                            month: 'long', day: 'numeric', year: 'numeric',
                            hour: 'numeric', minute: '2-digit'
                        });
                }

                // Re-render data
                renderRentCastData(data.data);
            } else {
                showToast('Error: ' + (data.error || 'Failed to refresh'), 'error');
            }
        }, remainingDelay);
    })
    .catch(err => {
        btn.innerHTML = originalText;
        btn.disabled = false;
        showToast('Network error. Please try again.', 'error');
    });
}

function renderRentCastData(data) {
    if (!data) return;

    // Sale History
    renderSaleHistory(data);

    // Property Details
    renderPropertyDetails(data);

    // Tax Assessments
    renderTaxAssessments(data);

    // Property Taxes
    renderPropertyTaxes(data);

    // Owner Information
    renderOwnerInfo(data);

    // Property Features
    renderFeatures(data);

    // Location Details
    renderLocation(data);
}

function renderSaleHistory(data) {
    const container = document.getElementById('sale-history-content');
    let html = '';

    // Last sale
    if (data.lastSaleDate || data.lastSalePrice) {
        html += `<div class="intel-row"><span class="intel-label">Last Sale Date</span><span class="intel-value">${formatDate(data.lastSaleDate)}</span></div>`;
        html += `<div class="intel-row"><span class="intel-label">Last Sale Price</span><span class="intel-value text-emerald-600 font-semibold">${formatCurrency(data.lastSalePrice)}</span></div>`;
    }

    // Previous sales from history
    if (data.history && data.history.length > 0) {
        const previousSales = data.history.filter(h => h.saleDate !== data.lastSaleDate);
        if (previousSales.length > 0) {
            html += `<div class="intel-row"><span class="intel-label font-medium text-slate-700 col-span-2 pt-2">Previous Sales</span></div>`;
            previousSales.slice(0, 5).forEach(sale => {
                html += `<div class="intel-row"><span class="intel-label">${formatDate(sale.saleDate)}</span><span class="intel-value">${formatCurrency(sale.salePrice)}</span></div>`;
            });
        }

        // Calculate appreciation if we have sales
        if (data.history.length >= 2) {
            const sortedHistory = [...data.history].sort((a, b) => new Date(a.saleDate) - new Date(b.saleDate));
            const oldestPrice = sortedHistory[0].salePrice;
            const newestPrice = sortedHistory[sortedHistory.length - 1].salePrice;
            const appreciation = calculateAppreciation(oldestPrice, newestPrice);
            if (appreciation) {
                const appreciationClass = appreciation.startsWith('+') ? 'text-emerald-600' : 'text-red-600';
                html += `<div class="intel-row border-t border-slate-200 mt-2 pt-2"><span class="intel-label font-medium">Total Appreciation</span><span class="intel-value ${appreciationClass} font-semibold">${appreciation}</span></div>`;
            }
        }
    }

    if (!html) {
        html = '<p class="text-slate-400 text-sm">No sale history available</p>';
    }

    container.innerHTML = html;
}

function renderPropertyDetails(data) {
    const container = document.getElementById('property-details-content');
    let html = '';

    const details = [
        { label: 'Property Type', value: data.propertyType },
        { label: 'Year Built', value: data.yearBuilt },
        { label: 'Bedrooms', value: data.bedrooms },
        { label: 'Bathrooms', value: data.bathrooms },
        { label: 'Square Footage', value: data.squareFootage ? formatNumber(data.squareFootage) + ' sqft' : null },
        { label: 'Lot Size', value: data.lotSize ? formatNumber(data.lotSize) + ' sqft' : null },
        { label: 'Stories', value: data.stories },
    ];

    // HOA
    if (data.hoa && data.hoa.fee) {
        details.push({ label: 'HOA Fee', value: formatCurrency(data.hoa.fee) + '/month' });
    }

    details.forEach(d => {
        if (d.value) {
            html += `<div class="intel-row"><span class="intel-label">${d.label}</span><span class="intel-value">${d.value}</span></div>`;
        }
    });

    if (!html) {
        html = '<p class="text-slate-400 text-sm">No property details available</p>';
    }

    container.innerHTML = html;
}

function renderTaxAssessments(data) {
    const container = document.getElementById('tax-assessments-content');
    let html = '';

    if (data.taxAssessments && Object.keys(data.taxAssessments).length > 0) {
        const years = Object.keys(data.taxAssessments).sort((a, b) => b - a).slice(0, 5);
        const values = years.map(y => data.taxAssessments[y]);

        years.forEach((year, i) => {
            const assessment = data.taxAssessments[year];
            const totalValue = assessment.value || assessment.total;
            const landValue = assessment.land;
            let valueStr = formatCurrency(totalValue);
            if (landValue) {
                valueStr += ` <span class="text-slate-400 text-xs">(Land: ${formatCurrency(landValue)})</span>`;
            }
            html += `<div class="intel-row"><span class="intel-label">${year}</span><span class="intel-value">${valueStr}</span></div>`;
        });

        // Calculate trend
        if (values.length >= 2) {
            const oldest = values[values.length - 1].value || values[values.length - 1].total;
            const newest = values[0].value || values[0].total;
            const trend = calculateAppreciation(oldest, newest);
            if (trend) {
                const trendClass = trend.startsWith('+') ? 'text-emerald-600' : 'text-red-600';
                html += `<div class="intel-row border-t border-slate-200 mt-2 pt-2"><span class="intel-label font-medium">${years.length}-Year Trend</span><span class="intel-value ${trendClass} font-semibold">${trend}</span></div>`;
            }
        }
    } else {
        html = '<p class="text-slate-400 text-sm">No tax assessment data available</p>';
    }

    container.innerHTML = html;
}

function renderPropertyTaxes(data) {
    const container = document.getElementById('property-taxes-content');
    let html = '';

    if (data.propertyTaxes && Object.keys(data.propertyTaxes).length > 0) {
        const years = Object.keys(data.propertyTaxes).sort((a, b) => b - a).slice(0, 5);
        let total = 0;

        years.forEach(year => {
            const tax = data.propertyTaxes[year];
            const taxAmount = tax.total || tax;
            total += taxAmount;
            html += `<div class="intel-row"><span class="intel-label">${year}</span><span class="intel-value">${formatCurrency(taxAmount)}</span></div>`;
        });

        // Average and monthly estimate
        const avgTax = total / years.length;
        const monthlyEst = avgTax / 12;
        html += `<div class="intel-row border-t border-slate-200 mt-2 pt-2"><span class="intel-label font-medium">Average (${years.length}yr)</span><span class="intel-value">${formatCurrency(avgTax)}/year</span></div>`;
        html += `<div class="intel-row"><span class="intel-label font-medium">Monthly Estimate</span><span class="intel-value text-amber-600 font-semibold">${formatCurrency(monthlyEst)}/month</span></div>`;
    } else {
        html = '<p class="text-slate-400 text-sm">No property tax data available</p>';
    }

    container.innerHTML = html;
}

function renderOwnerInfo(data) {
    const container = document.getElementById('owner-info-content');
    let html = '';

    if (data.owner) {
        const ownerName = data.owner.names ? data.owner.names[0] : null;
        if (ownerName) {
            html += `<div class="intel-row"><span class="intel-label">Owner Name</span><span class="intel-value">${ownerName}</span></div>`;
        }
        if (data.owner.type) {
            html += `<div class="intel-row"><span class="intel-label">Owner Type</span><span class="intel-value">${data.owner.type}</span></div>`;
        }
    }

    if (data.ownerOccupied !== undefined) {
        const occupiedText = data.ownerOccupied ? '<span class="text-emerald-600"><i class="fas fa-check mr-1"></i>Yes</span>' : '<span class="text-slate-400">No</span>';
        html += `<div class="intel-row"><span class="intel-label">Owner Occupied</span><span class="intel-value">${occupiedText}</span></div>`;
    }

    if (data.owner && data.owner.mailingAddress) {
        const addr = data.owner.mailingAddress;
        const formattedAddr = addr.formattedAddress || `${addr.street || ''}, ${addr.city || ''}, ${addr.state || ''} ${addr.zip || ''}`;
        html += `<div class="intel-row"><span class="intel-label">Mailing Address</span><span class="intel-value text-xs">${formattedAddr}</span></div>`;
    }

    if (!html) {
        html = '<p class="text-slate-400 text-sm">No owner information available</p>';
    }

    container.innerHTML = html;
}

function renderFeatures(data) {
    const container = document.getElementById('features-content');
    let html = '';

    if (data.features && Object.keys(data.features).length > 0) {
        const features = data.features;
        const featureList = [
            { label: 'Architecture', value: features.architectureType },
            { label: 'Cooling', value: features.cooling },
            { label: 'Heating', value: features.heating },
            { label: 'Garage', value: features.garage ? `${features.garageSpaces || ''} spaces, ${features.garageType || features.garage}` : null },
            { label: 'Pool', value: features.pool },
            { label: 'Fireplace', value: features.fireplace ? `Yes${features.fireplaceType ? ' (' + features.fireplaceType + ')' : ''}` : null },
            { label: 'Exterior', value: features.exteriorType },
            { label: 'Roof', value: features.roofType },
            { label: 'Foundation', value: features.foundationType },
            { label: 'Total Rooms', value: features.roomsTotal },
            { label: 'View', value: features.viewType },
        ];

        featureList.forEach(f => {
            if (f.value) {
                html += `<div class="intel-row"><span class="intel-label">${f.label}</span><span class="intel-value">${f.value}</span></div>`;
            }
        });
    }

    if (!html) {
        html = '<p class="text-slate-400 text-sm">No feature data available</p>';
    }

    container.innerHTML = html;
}

function renderLocation(data) {
    const container = document.getElementById('location-content');
    let html = '';

    const location = [
        { label: 'County', value: data.county },
        { label: 'Subdivision', value: data.subdivision },
        { label: 'Zoning', value: data.zoning },
        { label: 'Assessor ID', value: data.assessorID },
    ];

    if (data.latitude && data.longitude) {
        location.push({ label: 'Coordinates', value: `${data.latitude.toFixed(4)}, ${data.longitude.toFixed(4)}` });
    }

    if (data.legalDescription) {
        location.push({ label: 'Legal Description', value: data.legalDescription.length > 50 ? data.legalDescription.substring(0, 50) + '...' : data.legalDescription });
    }

    location.forEach(l => {
        if (l.value) {
            html += `<div class="intel-row"><span class="intel-label">${l.label}</span><span class="intel-value">${l.value}</span></div>`;
        }
    });

    if (!html) {
        html = '<p class="text-slate-400 text-sm">No location data available</p>';
    }

    container.innerHTML = html;
}

function toggleFeatures() {
    const content = document.getElementById('features-content-wrapper');
    const chevron = document.getElementById('features-chevron');
    content.classList.toggle('expanded');
    chevron.style.transform = content.classList.contains('expanded') ? 'rotate(180deg)' : 'rotate(0deg)';
}

// Initialize if we have data on page load
if (currentRentCastData) {
    renderRentCastData(currentRentCastData);
}
