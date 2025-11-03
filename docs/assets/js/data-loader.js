// Data Loader Utility
// Handles loading and processing all trading data

class DataLoader {
    constructor() {
        this.agentData = {};
        this.priceCache = {};
        this.config = null;
        // Default from config; will be set in initialize()
        this.baseDataPath = './data';
    }

    // Initialize with configuration
    async initialize() {
        if (!this.config) {
            this.config = await window.configLoader.loadConfig();
            this.baseDataPath = window.configLoader.getDataPath();
        }
    }

    // Internal: fetch JSON with fallback between configured base and the alternate path
    async _fetchJSONWithFallback(relativePath) {
        const ts = Date.now();
        const tryPaths = [this.baseDataPath, (this.baseDataPath === './data' ? '../data' : './data')];
        let lastErr = null;
        for (const base of tryPaths) {
            try {
                const url = `${base}/${relativePath}?t=${ts}`;
                const res = await fetch(url);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                // Update base on success
                this.baseDataPath = base;
                return data;
            } catch (e) {
                lastErr = e;
            }
        }
        throw lastErr || new Error('Fetch failed');
    }

    // Internal: fetch text (for jsonl) with fallback
    async _fetchTextWithFallback(relativePath) {
        const ts = Date.now();
        const tryPaths = [this.baseDataPath, (this.baseDataPath === './data' ? '../data' : './data')];
        let lastErr = null;
        for (const base of tryPaths) {
            try {
                const url = `${base}/${relativePath}?t=${ts}`;
                const res = await fetch(url);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const text = await res.text();
                this.baseDataPath = base;
                return text;
            } catch (e) {
                lastErr = e;
            }
        }
        throw lastErr || new Error('Fetch failed');
    }

    // Load all agent names from configuration (restricted to DeepSeek Terminus)
    async loadAgentList() {
        // Ensure config is loaded
        await this.initialize();

        try {
            // Only keep DeepSeek Terminus regardless of other enabled agents
            const enabledAgents = window.configLoader
                .getEnabledAgents()
                .filter(a => a.folder === 'deepseek-ai/DeepSeek-V3.1-Terminus');
            const agents = [];

            for (const agentConfig of enabledAgents) {
                try {
                    console.log(`Checking agent: ${agentConfig.folder}`);
                    // Probe file existence with fallback logic
                    try {
                        await this._fetchTextWithFallback(`agent_data/${agentConfig.folder}/position/position.jsonl`);
                        agents.push(agentConfig.folder);
                        console.log(`Added agent: ${agentConfig.folder}`);
                    } catch (probeErr) {
                        console.log(`Agent ${agentConfig.folder} not found:`, probeErr.message);
                    }
                } catch (e) {
                    console.log(`Agent ${agentConfig.folder} error:`, e.message);
                }
            }

            return agents;
        } catch (error) {
            console.error('Error loading agent list:', error);
            return [];
        }
    }

    // Load position data for a specific agent
    async loadAgentPositions(agentName) {
        try {
            const text = await this._fetchTextWithFallback(`agent_data/${agentName}/position/position.jsonl`);
            const lines = text.trim().split('\n').filter(line => line.trim() !== '');
            const positions = lines.map(line => {
                try {
                    return JSON.parse(line);
                } catch (parseError) {
                    console.error(`Error parsing line for ${agentName}:`, line, parseError);
                    return null;
                }
            }).filter(pos => pos !== null);

            console.log(`Loaded ${positions.length} positions for ${agentName}`);
            return positions;
        } catch (error) {
            console.error(`Error loading positions for ${agentName}:`, error);
            return [];
        }
    }

    // Load price data for a specific stock symbol
    async loadStockPrice(symbol) {
        if (this.priceCache[symbol]) {
            return this.priceCache[symbol];
        }

        try {
            const priceFilePrefix = window.configLoader.getPriceFilePrefix();
            const data = await this._fetchJSONWithFallback(`${priceFilePrefix}${symbol}.json`);
            // Support both hourly (60min) and daily data formats
            this.priceCache[symbol] = data['Time Series (60min)'] || data['Time Series (Daily)'];
            return this.priceCache[symbol];
        } catch (error) {
            console.error(`Error loading price for ${symbol}:`, error);
            return null;
        }
    }

    // Load prices ensuring we have the best available for a specific date
    async loadStockPriceEnsure(symbol, date) {
        let prices = this.priceCache[symbol] || null;
        const needRefetch = !prices || !prices[date] || prices[date]['4. close'] === undefined;
        if (needRefetch) {
            try {
                const priceFilePrefix = window.configLoader.getPriceFilePrefix();
                const data = await this._fetchJSONWithFallback(`${priceFilePrefix}${symbol}.json`);
                this.priceCache[symbol] = data['Time Series (60min)'] || data['Time Series (Daily)'];
                prices = this.priceCache[symbol];
            } catch (e) {
                console.error(`Error refreshing price for ${symbol}:`, e);
            }
        }
        return prices;
    }

    // Resolve a date key for a prices dictionary that may be hourly or daily
    _resolveDateKey(prices, date) {
        if (!prices) return date;
        // If exact match exists, use it
        if (prices[date]) return date;
        const hasHourly = Object.keys(prices).some(k => k.includes(':'));
        const isDailyInput = !date.includes(':');
        if (hasHourly && isDailyInput) {
            // Find last available bar for that day
            const prefix = `${date} `;
            const candidates = Object.keys(prices).filter(k => k.startsWith(prefix)).sort();
            if (candidates.length > 0) {
                return candidates[candidates.length - 1];
            }
        }
        return date;
    }

    // Get closing price for a symbol on a specific date
    async getClosingPrice(symbol, date) {
        const prices = await this.loadStockPriceEnsure(symbol, date);
        if (!prices) {
            return null;
        }
        const key = this._resolveDateKey(prices, date);
        const bar = prices[key];
        if (!bar) return null;
        const close = bar['4. close'];
        if (close !== undefined && close !== null && close !== '') {
            return parseFloat(close);
        }
        // Fallback: if close is not available (e.g., intraday), use today's buy price to estimate
        const openBuy = bar['1. buy price'] || bar['1. open'];
        return openBuy ? parseFloat(openBuy) : null;
    }

    // Calculate total asset value for a position on a given date
    async calculateAssetValue(position, date) {
        let totalValue = position.positions.CASH || 0;

        // Get all stock symbols (exclude CASH)
        const symbols = Object.keys(position.positions).filter(s => s !== 'CASH');

        for (const symbol of symbols) {
            const shares = position.positions[symbol];
            if (shares > 0) {
                const price = await this.getClosingPrice(symbol, date);
                if (price) {
                    totalValue += shares * price;
                }
            }
        }

        return totalValue;
    }

    // Load complete data for an agent including asset values over time
    async loadAgentData(agentName) {
        console.log(`Starting to load data for ${agentName}...`);
        const positions = await this.loadAgentPositions(agentName);
        if (positions.length === 0) {
            console.log(`No positions found for ${agentName}`);
            return null;
        }

        console.log(`Processing ${positions.length} positions for ${agentName}...`);

        // Group positions by timestamp and take only the last position for each timestamp
        const positionsByTimestamp = {};
        positions.forEach(position => {
            const timestamp = position.date;
            if (!positionsByTimestamp[timestamp] || position.id > positionsByTimestamp[timestamp].id) {
                positionsByTimestamp[timestamp] = position;
            }
        });

        // Convert to array and sort by timestamp
        const uniquePositions = Object.values(positionsByTimestamp).sort((a, b) => {
            if (a.date !== b.date) {
                return a.date.localeCompare(b.date);
            }
            return a.id - b.id;
        });

        console.log(`Reduced from ${positions.length} to ${uniquePositions.length} unique hourly positions for ${agentName}`);

        const assetHistory = [];

        for (const position of uniquePositions) {
            const timestamp = position.date;
            const assetValue = await this.calculateAssetValue(position, timestamp);
            assetHistory.push({
                date: timestamp,
                value: assetValue,
                id: position.id,
                action: position.this_action || null
            });
        }

        const result = {
            name: agentName,
            positions: positions,
            assetHistory: assetHistory,
            initialValue: assetHistory[0]?.value || 10000,
            currentValue: assetHistory[assetHistory.length - 1]?.value || 0,
            return: assetHistory.length > 0 ?
                ((assetHistory[assetHistory.length - 1].value - assetHistory[0].value) / assetHistory[0].value * 100) : 0
        };

        console.log(`Successfully loaded data for ${agentName}:`, {
            positions: positions.length,
            assetHistory: assetHistory.length,
            initialValue: result.initialValue,
            currentValue: result.currentValue,
            return: result.return
        });

        return result;
    }

    // Load QQQ invesco data
    async loadQQQData() {
        try {
            console.log('Loading QQQ invesco data...');
            const benchmarkFile = window.configLoader.getBenchmarkFile();
            const data = await this._fetchJSONWithFallback(benchmarkFile);
            // Support both hourly (60min) and daily data formats
            const timeSeries = data['Time Series (60min)'] || data['Time Series (Daily)'];
            
            // Convert to asset history format
            const assetHistory = [];
            const dates = Object.keys(timeSeries).sort();
            
            // Calculate QQQ performance starting from first agent's initial value
            const agentNames = Object.keys(this.agentData);
            const uiConfig = window.configLoader.getUIConfig();
            let initialValue = uiConfig.initial_value; // Default initial value from config
            
            if (agentNames.length > 0) {
                const firstAgent = this.agentData[agentNames[0]];
                if (firstAgent && firstAgent.assetHistory.length > 0) {
                    initialValue = firstAgent.assetHistory[0].value;
                }
            }
            
            // Find the earliest start date and latest end date across all agents
            let startDate = null;
            let endDate = null;
            if (agentNames.length > 0) {
                agentNames.forEach(agentName => {
                    const agent = this.agentData[agentName];
                    if (agent && agent.assetHistory.length > 0) {
                        const agentStartDate = agent.assetHistory[0].date;
                        const agentEndDate = agent.assetHistory[agent.assetHistory.length - 1].date;
                        
                        if (!startDate || agentStartDate < startDate) {
                            startDate = agentStartDate;
                        }
                        if (!endDate || agentEndDate > endDate) {
                            endDate = agentEndDate;
                        }
                    }
                });
            }
            
            let qqqStartPrice = null;
            let currentValue = initialValue;
            
            for (const date of dates) {
                if (startDate && date < startDate) continue;
                if (endDate && date > endDate) continue;
                
                const price = parseFloat(timeSeries[date]['4. close']);
                if (!qqqStartPrice) {
                    qqqStartPrice = price;
                }
                
                // Calculate QQQ performance relative to start
                const qqqReturn = (price - qqqStartPrice) / qqqStartPrice;
                currentValue = initialValue * (1 + qqqReturn);
                
                assetHistory.push({
                    date: date,
                    value: currentValue,
                    id: `qqq-${date}`,
                    action: null
                });
            }

            const result = {
                name: window.configLoader.getBenchmarkConfig().folder,
                positions: [],
                assetHistory: assetHistory,
                initialValue: initialValue,
                currentValue: assetHistory.length > 0 ? assetHistory[assetHistory.length - 1].value : initialValue,
                return: assetHistory.length > 0 ?
                    ((assetHistory[assetHistory.length - 1].value - assetHistory[0].value) / assetHistory[0].value * 100) : 0
            };

            console.log('Successfully loaded benchmark data:', {
                assetHistory: assetHistory.length,
                initialValue: result.initialValue,
                currentValue: result.currentValue,
                return: result.return
            });
            
            return result;
        } catch (error) {
            console.error('Error loading QQQ data:', error);
            return null;
        }
    }

    // Load all agents data
    async loadAllAgentsData() {
        console.log('Starting to load all agents data...');
        const agents = await this.loadAgentList();
        console.log('Found agents:', agents);
        const allData = {};

        for (const agent of agents) {
            console.log(`Loading data for ${agent}...`);
            const data = await this.loadAgentData(agent);
            if (data) {
                allData[agent] = data;
                console.log(`Successfully added ${agent} to allData`);
            } else {
                console.log(`Failed to load data for ${agent}`);
            }
        }

        console.log('Final allData:', Object.keys(allData));
        this.agentData = allData;

        // Load benchmark data if enabled
        const benchmarkConfig = window.configLoader.getBenchmarkConfig();
        if (benchmarkConfig && benchmarkConfig.enabled) {
            const benchmarkData = await this.loadQQQData();
            if (benchmarkData) {
                allData[benchmarkConfig.folder] = benchmarkData;
                console.log(`Successfully added ${benchmarkConfig.display_name} to allData`);
            }
        }

        return allData;
    }

    // Get current holdings for an agent (latest position)
    getCurrentHoldings(agentName) {
        const data = this.agentData[agentName];
        if (!data || !data.positions || data.positions.length === 0) return null;

        const latestPosition = data.positions[data.positions.length - 1];
        return latestPosition && latestPosition.positions ? latestPosition.positions : null;
    }

    // Get trade history for an agent
    getTradeHistory(agentName) {
        const data = this.agentData[agentName];
        if (!data) return [];

        return data.positions
            .filter(p => p.this_action)
            .map(p => ({
                date: p.date,
                action: p.this_action.action,
                symbol: p.this_action.symbol,
                amount: p.this_action.amount
            }))
            .reverse(); // Most recent first
    }

    // Format number as currency
    formatCurrency(value) {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: 2
        }).format(value);
    }

    // Format percentage
    formatPercent(value) {
        const sign = value >= 0 ? '+' : '';
        return `${sign}${value.toFixed(2)}%`;
    }

    // Get nice display name for agent
    getAgentDisplayName(agentName) {
        return window.configLoader.getDisplayName(agentName);
    }

    // Get icon for agent (SVG file path)
    getAgentIcon(agentName) {
        return window.configLoader.getIcon(agentName);
    }

    // Get agent name without version suffix for icon lookup
    getAgentIconKey(agentName) {
        // This method is kept for backward compatibility
        return agentName;
    }

    // Get brand color for agent
    getAgentBrandColor(agentName) {
        return window.configLoader.getColor(agentName);
    }
}

// Export for use in other modules
window.DataLoader = DataLoader;
