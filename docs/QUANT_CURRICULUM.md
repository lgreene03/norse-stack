# Quantitative Trading Curriculum

*A ground-up guide to quantitative crypto trading, taught through the Norse Stack.*

---

## How to Use This Document

This curriculum assumes you know nothing about trading, finance, or cryptocurrency. Each chapter introduces one concept, explains why it matters, and then shows exactly how the Norse Stack implements it. Read the chapters in order -- each one builds on the last.

If you want the fastest path to understanding the system end-to-end, follow the [Learning Path](#learning-path) at the bottom.

---

## Chapter 1: What Is Trading and What Is a Market?

**The concept.** A market is anywhere buyers and sellers meet to exchange things. A stock market lets you trade shares of companies. A crypto market lets you trade digital currencies like Bitcoin and Ethereum. "Trading" just means buying something at one price and selling it at another, hoping the sell price is higher than the buy price.

**Why it matters.** If you want to make money from price movements, you need a market that lets you act on them. Crypto markets run 24/7, unlike stock markets that close overnight. This means opportunities (and risks) never stop.

**How Norse Stack does it.** Muninn connects directly to the Binance exchange via WebSocket streams. Binance is one of the largest crypto exchanges in the world -- it is the market. Muninn ingests raw market data (prices, volumes, order book snapshots) around the clock and stores it in Postgres for historical analysis and S3 for bulk feature storage. This is the foundation everything else rests on: without market data, there is nothing to trade.

---

## Chapter 2: Order Books and Price Discovery

**The concept.** An order book is a list of all the buy orders ("bids") and sell orders ("asks") for a given asset, arranged by price. The highest bid and the lowest ask define the "spread" -- the gap between what buyers want to pay and what sellers want to accept. When a bid and an ask meet, a trade happens. This process is called "price discovery": the market collectively figures out what something is worth, moment by moment.

**Why it matters.** The order book tells you more than just the current price. It tells you how much buying or selling pressure exists at each price level. If there are huge buy orders stacked up just below the current price, the price has support. If there are huge sell orders just above, the price faces resistance.

**How Norse Stack does it.** Obi-Bridge computes **Order Book Imbalance (OBI)** -- the first of its 10 signal layers. OBI measures the ratio of bid volume to ask volume near the current price. If bids heavily outweigh asks, buyers are more aggressive, and prices may rise. Muninn captures the raw order book snapshots from Binance, and Obi-Bridge transforms them into a single number that downstream systems can reason about.

---

## Chapter 3: Technical Indicators and Signals

**The concept.** A "technical indicator" is a mathematical formula applied to price and volume data to extract a pattern. For example, a "moving average" smooths out noisy price data to reveal trends. Traders have invented hundreds of these indicators. A "signal" is the actionable output: a number or flag that suggests whether conditions favor buying, selling, or doing nothing.

**Why it matters.** Raw price data is noisy. A price chart bounces around every second. Indicators distill that noise into something a trading system (or a human) can act on. One indicator alone is unreliable. Combining many gives you a richer, more robust picture.

**How Norse Stack does it.** Obi-Bridge computes 10 distinct signal layers, each capturing a different dimension of market state:

- **Multi-timeframe momentum** (signal 2): Measures whether the price is trending up or down across 1-minute, 5-minute, and 15-minute windows. A signal that is bullish across all three timeframes is more convincing than one that only shows up on a 1-minute chart.
- **Volatility regime** (signal 3): Uses Bollinger Band width and Average True Range (ATR) to determine whether the market is calm or chaotic. Strategies that work in calm markets often fail in volatile ones.
- **Volume ratio** (signal 4): Compares current trading volume to recent averages. A price move on high volume is more meaningful than one on low volume.
- **Fear and Greed Index** (signal 5): A sentiment indicator that captures overall market mood. Extreme fear can signal buying opportunities; extreme greed can signal overextension.
- **Funding rate** (signal 6): Specific to perpetual futures contracts. When funding is very positive, longs are paying shorts, indicating crowded bullish positioning. This can precede reversals.
- **Open interest cascade** (signal 7): Tracks the total number of open futures contracts. Rapid changes can signal incoming liquidation cascades.
- **Regime detection** (signal 10): Uses the Hurst exponent and autocorrelation to classify whether the market is trending, mean-reverting, or random-walking. This determines which type of strategy should be active.

Each signal is computed in real time and published for downstream consumption by Huginn.

---

## Chapter 4: Strategy Logic -- When to Buy and Sell

**The concept.** A trading strategy is a set of rules that takes signals as input and produces trading decisions as output. "If momentum is positive AND volatility is low AND the ML model is confident, buy." The rules can be simple or complex, but they must be unambiguous -- a computer cannot interpret "it feels like a good time to buy."

**Why it matters.** Without a strategy, you are gambling. A strategy gives you a repeatable, testable process. You can run it against historical data (backtesting) to see how it would have performed, and you can measure whether its edge is real or just luck.

**How Norse Stack does it.** Huginn is the strategy execution engine. It consumes the 10 signal layers from Obi-Bridge, applies strategy logic, and produces trade decisions. Huginn supports **walk-forward backtesting**, which is more rigorous than simple backtesting. In walk-forward testing, you optimize your strategy on a window of historical data, then test it on the next unseen window, then slide forward and repeat. This prevents "overfitting" -- the trap of designing a strategy that performs perfectly on past data but fails on new data because it memorized noise rather than learning real patterns.

Huginn manages the portfolio: which positions are open, how large they are, and when they should be closed.

---

## Chapter 5: Risk Management -- Why You Need It and How It Works

**The concept.** Risk management is the discipline of controlling how much you can lose. Even the best strategy will have losing trades. The question is whether a bad streak will wipe you out or just dent your account. Key concepts:

- **Position sizing**: How much of your capital to put into each trade. Bet too big and one loss is catastrophic. Bet too small and your wins are meaningless.
- **Value at Risk (VaR)**: A statistical estimate of the maximum you could lose over a given time period with a given probability. "There is a 95% chance we will not lose more than $X today."
- **Conditional VaR (CVaR)**: Also called Expected Shortfall. VaR tells you the threshold; CVaR tells you the average loss when things are worse than the threshold. It answers "when things go badly, how bad do they get?"
- **Drawdown**: The peak-to-trough decline in your portfolio value. A 50% drawdown means you need a 100% gain just to get back to even.

**Why it matters.** Markets can move against you faster than you expect. Crypto is especially volatile -- 10-20% daily moves are not unusual. Without risk management, a single bad day can destroy months of gains. Professional traders often say that managing risk is more important than finding good trades.

**How Norse Stack does it.** Multiple layers of risk control are spread across the stack:

- **Huginn** uses the **Kelly criterion** for position sizing. The Kelly criterion is a formula that calculates the mathematically optimal bet size given your edge (win rate and win/loss ratio). It maximizes long-term growth rate while minimizing the chance of ruin. In practice, Norse Stack uses a fractional Kelly (betting less than the full Kelly amount) to be more conservative.
- **Odin** computes **Portfolio VaR** using the variance-covariance method. This takes the portfolio's holdings, their individual volatilities, and their correlations to estimate the worst-case loss at a given confidence level.
- **Odin** also computes **CVaR** and runs **Monte Carlo simulations** to stress-test the portfolio against thousands of randomized scenarios.
- **Sleipnir** enforces hard risk checks at the execution layer -- even if Huginn sends a reckless order, Sleipnir will reject it if it violates risk limits. This is defense in depth: risk rules exist at both the strategy level and the execution level.

---

## Chapter 6: Execution -- Why HOW You Trade Matters

**The concept.** Execution is the process of turning a trading decision into an actual order on the exchange. This sounds trivial but it is not. If you want to buy $100,000 worth of Bitcoin, you cannot just slam a single market order. That would "move the market" against you -- your own buying would push the price up, and you would end up paying more than the price you saw when you decided to buy. This is called "slippage."

Execution algorithms break large orders into smaller pieces and spread them over time to minimize market impact:

- **TWAP (Time-Weighted Average Price)**: Splits the order into equal-sized pieces and executes them at regular intervals. Simple and predictable.
- **VWAP (Volume-Weighted Average Price)**: Splits the order proportional to expected volume patterns. Executes more during high-volume periods (when your order is less noticeable) and less during low-volume periods.

**Why it matters.** Poor execution can eat your entire trading edge. If your strategy expects to make 0.1% per trade but you lose 0.15% to slippage, you are losing money on every winning trade. In crypto, where markets are fragmented and liquidity can be thin, execution quality is a genuine competitive advantage.

**How Norse Stack does it.** Sleipnir is the dedicated order execution gateway. It implements both TWAP and VWAP algorithms, handles rate limiting (exchanges cap how many orders you can send per second), and provides the actual connectivity to the exchange. Sleipnir also tracks **signal-to-decision latency** -- the time between a signal being generated and an order being placed. The system achieves a median latency of approximately 6 milliseconds. In trading, speed matters: the faster you can act on a signal, the more likely the opportunity still exists when your order arrives.

---

## Chapter 7: Performance Measurement -- Are You Actually Good?

**The concept.** After running a strategy, you need to measure whether it actually works. Raw profit is not enough -- you need to know whether your returns are good relative to the risk you took. Key metrics:

- **Sharpe ratio**: Return divided by volatility (risk). A Sharpe of 1.0 means you earned 1 unit of return for every unit of risk. Above 2.0 is very good. Below 0.5 is questionable.
- **Sortino ratio**: Like Sharpe, but only penalizes downside volatility. Upside volatility (big wins) is not treated as risk, which is arguably more fair.
- **Maximum drawdown**: The worst peak-to-trough decline. Tells you the worst pain you would have experienced.
- **Statistical significance**: Is your performance real or just luck? If you flip a coin 10 times and get 7 heads, that might be chance. If you flip it 10,000 times and get 7,000 heads, the coin is probably rigged.

**Why it matters.** Many strategies look profitable in backtests but fail in live trading. Rigorous performance measurement separates real edges from illusions. Without it, you are flying blind.

**How Norse Stack does it.** Odin is the performance analytics service. It computes Sharpe ratio, Sortino ratio, CVaR, and maximum drawdown. Critically, it also runs a **Monte Carlo permutation test** for strategy significance. This test randomly shuffles your trade returns thousands of times and compares your actual performance to the shuffled versions. If your real results are better than 95% or more of the random shuffles, your strategy likely has a genuine edge rather than a lucky streak.

Bragi complements Odin by providing **trade explainability** -- for any given trade, it can tell you which signals contributed to the decision and by how much. This is essential for debugging strategies and understanding why a trade was taken.

---

## Chapter 8: Machine Learning in Trading

**The concept.** Machine learning (ML) models find patterns in data that humans and simple rules might miss. In trading, you feed the model historical features (indicators, signals, market conditions) and the outcome you want to predict (did the price go up or down?). The model learns a mapping from features to outcomes and can then make predictions on new, unseen data.

**Why it matters.** Markets are complex, nonlinear systems. A simple rule like "buy when momentum is positive" might work sometimes, but it ignores the interaction between momentum, volatility, sentiment, and dozens of other factors. ML models can capture these interactions. The risk is overfitting: a model that memorizes historical noise rather than learning real patterns.

**How Norse Stack does it.** Huginn-AI uses **XGBoost**, a gradient-boosted decision tree algorithm. XGBoost is popular in quantitative finance because it handles tabular data well, is relatively interpretable compared to deep learning, and is resistant to overfitting when properly regularized. Huginn-AI's output becomes signal layer 8 -- the ML confidence score -- which feeds into Huginn's strategy logic alongside the other 9 signals. The ML model does not trade on its own; it is one voice among ten, and the strategy engine decides how much weight to give it.

Additionally, Obi-Bridge's signal layer 9 uses **Ollama** (a local LLM runner) to process news sentiment. This is a separate ML application: a large language model reads news headlines and classifies them as bullish, bearish, or neutral for a given asset.

---

## Chapter 9: System Architecture -- Why Distributed?

**The concept.** A monolithic system puts everything in one application. A distributed system splits responsibilities across multiple services that communicate over the network. Each service does one thing well.

**Why it matters in trading.** Trading systems have conflicting requirements. Market data ingestion needs to handle high throughput. Strategy computation needs low latency. Order execution needs reliability. Performance analytics can tolerate higher latency but needs access to large datasets. Trying to satisfy all of these in a single application leads to painful compromises.

**How Norse Stack does it.** The Norse Stack is split into seven services, each with its own language chosen for the task:

| Service | Language | Responsibility |
|---------|----------|---------------|
| Muninn | Java/Spring Boot | Market data ingestion, feature computation, storage |
| Huginn | Go | Strategy execution, portfolio management |
| Sleipnir | Go | Order execution, rate limiting, risk checks |
| Obi-Bridge | Python | Real-time signal computation (10 layers) |
| Odin | Python | Performance analytics |
| Bragi | Python | Trade explainability |
| Huginn-AI | Python | ML signal prediction |

Java and Go handle the latency-sensitive, high-throughput paths (data ingestion, strategy execution, order routing). Python handles the analytically complex paths where library ecosystems (NumPy, scikit-learn, XGBoost) matter more than raw speed.

Services communicate via **gRPC**, a high-performance RPC framework that uses Protocol Buffers for serialization. This is significantly faster than REST/JSON for inter-service communication. The system also exposes a gRPC API for programmatic access, so external tools and scripts can query signals, submit orders, or pull analytics.

Data flows through the system in a pipeline: Binance exchange data enters through Muninn, flows to Obi-Bridge for signal computation, to Huginn for strategy decisions, and to Sleipnir for execution. Odin and Bragi operate on the output side, analyzing completed trades.

---

## Chapter 10: Observability and Production Monitoring

**The concept.** "Observability" means being able to understand what your system is doing from the outside, without having to change its code. The three pillars are metrics (numbers over time), logs (timestamped event records), and traces (following a request through the system). When your trading system runs 24/7 with real money, you need to know immediately when something goes wrong.

**Why it matters.** A bug in a trading system does not just cause a 500 error page -- it causes real financial losses. If your signal computation silently starts returning stale data, your strategy is trading on lies. If your order execution slows down, you are losing money to latency. If a service crashes at 3 AM, you need to know before the market moves against your open positions.

**How Norse Stack does it.** The stack uses **Prometheus** for metrics collection and **Grafana** for visualization. An 11-panel Grafana dashboard provides a unified view of system health:

- **Signal-to-decision latency**: Tracks how quickly the system converts market data into trade decisions. The median is approximately 6ms. A spike here means something in the pipeline is bottlenecked.
- **Signal layer health**: Monitors each of the 10 signal layers for freshness and validity. Stale or NaN signals trigger alerts.
- **Order execution rates**: Tracks fill rates, rejection rates, and partial fills at Sleipnir.
- **Portfolio exposure**: Real-time view of position sizes and risk utilization across the Huginn portfolio.
- **System resource usage**: CPU, memory, and network across all services.

Every service exports Prometheus metrics, and alerts are configured for critical conditions: service downtime, abnormal latency spikes, risk limit breaches, and data staleness. The goal is that no failure goes unnoticed for more than a few minutes.

---

## Learning Path

If you are starting from zero, follow this order:

1. **Weeks 1-2**: Read Chapters 1-3. Focus on understanding markets, order books, and signals. Explore the Binance web interface to see a live order book. Read Obi-Bridge's signal computation code to see how raw data becomes signals.

2. **Weeks 3-4**: Read Chapters 4-5. Focus on strategy logic and risk management. Study Huginn's strategy configuration to understand how signals are combined into decisions. Run a walk-forward backtest on historical data.

3. **Weeks 5-6**: Read Chapters 6-7. Focus on execution and performance measurement. Study Sleipnir's TWAP/VWAP implementations. Use Odin to analyze backtest results -- pay special attention to the Monte Carlo permutation test.

4. **Weeks 7-8**: Read Chapter 8. Train a simple XGBoost model on historical features. Compare the ML signal's predictions against actual outcomes. Understand why ensemble approaches (combining ML with traditional signals) are more robust than pure ML.

5. **Weeks 9-10**: Read Chapters 9-10. Study the system architecture, gRPC contracts, and Grafana dashboards. Run the full stack locally and trace a signal from Binance WebSocket through to order execution.

**Prerequisite knowledge to build along the way:**
- Basic statistics (mean, standard deviation, correlation, probability distributions)
- Basic programming (any language; Python is the most accessible entry point)
- Command-line comfort (Docker, gRPC tools, Prometheus queries)

---

## Glossary

**Ask**: The lowest price a seller is willing to accept. Also called the "offer."

**ATR (Average True Range)**: A volatility indicator measuring the average range between high and low prices over a period.

**Backtest**: Running a strategy against historical data to evaluate how it would have performed.

**Bid**: The highest price a buyer is willing to pay.

**Bollinger Bands**: A volatility indicator consisting of a moving average with upper and lower bands at a set number of standard deviations away.

**CVaR (Conditional Value at Risk)**: The expected loss in the worst X% of scenarios. Also called Expected Shortfall.

**Drawdown**: The decline from a portfolio's peak value to its lowest point before a new peak.

**Funding Rate**: In perpetual futures, a periodic payment between long and short holders that keeps the futures price anchored to the spot price.

**gRPC**: A high-performance remote procedure call framework using Protocol Buffers for serialization.

**Hurst Exponent**: A measure of long-term memory in a time series. H > 0.5 suggests trending behavior; H < 0.5 suggests mean-reversion; H = 0.5 suggests a random walk.

**Kelly Criterion**: A formula for calculating the optimal bet size that maximizes the long-term growth rate of capital.

**Liquidation**: Forced closure of a leveraged position when losses exceed the margin deposited.

**Monte Carlo Simulation**: A technique that generates thousands of random scenarios to estimate the probability distribution of outcomes.

**OBI (Order Book Imbalance)**: The ratio of bid volume to ask volume near the current price, indicating buying or selling pressure.

**Open Interest**: The total number of outstanding futures or options contracts that have not been settled.

**Overfitting**: When a model learns noise in historical data rather than genuine patterns, leading to poor performance on new data.

**Perpetual Futures**: Futures contracts with no expiration date, common in crypto markets.

**Sharpe Ratio**: Risk-adjusted return metric. (Return - Risk-Free Rate) / Standard Deviation of Returns.

**Slippage**: The difference between the expected price of a trade and the actual execution price.

**Sortino Ratio**: Like the Sharpe ratio, but uses only downside deviation instead of total standard deviation.

**Spread**: The difference between the best bid and best ask prices.

**TWAP (Time-Weighted Average Price)**: An execution algorithm that splits an order into equal pieces executed at regular time intervals.

**VaR (Value at Risk)**: A statistical measure of the maximum expected loss over a specified time period at a given confidence level.

**VWAP (Volume-Weighted Average Price)**: An execution algorithm that distributes order size proportional to expected volume patterns.

**Walk-Forward Testing**: A backtesting method that repeatedly optimizes on one data window and tests on the next unseen window to avoid overfitting.

**XGBoost**: An efficient gradient-boosted decision tree algorithm widely used in quantitative finance for tabular prediction tasks.
