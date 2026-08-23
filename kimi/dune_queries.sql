-- ============================================================
-- DUNE ANALYTICS SQL QUERIES FOR UNISWAP V3 LP BACKTEST
-- ============================================================
-- Run these at https://dune.com/queries and export CSV
-- Free tier: 4,000 credits/month

-- --------------------------------------------------------
-- QUERY 1: Daily Pool Volume & Fees (ETH/USDC 0.05%)
-- --------------------------------------------------------
SELECT 
    DATE_TRUNC('day', block_time) AS day,
    SUM(amount_usd) AS volume_usd,
    COUNT(*) AS trade_count,
    SUM(amount_usd * 0.0005) AS estimated_fees_usd,
    AVG(amount_usd) AS avg_trade_size_usd
FROM dex.trades
WHERE project = 'uniswap'
  AND version = '3'
  AND token_bought_address = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2  -- WETH
  AND token_sold_address = 0xA0b86a33E6441E6C7D3D4B4E5E6F7A8B9C0D1E2F  -- USDC
  AND fee_tier = 500  -- 0.05% = 500 bps
  AND block_time >= DATE('2024-01-01')
GROUP BY 1
ORDER BY 1

-- --------------------------------------------------------
-- QUERY 2: Hourly Pool Stats (more granular)
-- --------------------------------------------------------
SELECT 
    DATE_TRUNC('hour', block_time) AS hour,
    SUM(amount_usd) AS volume_usd,
    COUNT(*) AS trade_count,
    SUM(amount_usd * 0.0005) AS estimated_fees_usd
FROM dex.trades
WHERE project = 'uniswap'
  AND version = '3'
  AND token_bought_address = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2
  AND token_sold_address = 0xA0b86a33E6441E6C7D3D4B4E5E6F7A8B9C0D1E2F
  AND fee_tier = 500
  AND block_time >= NOW() - INTERVAL '90' day
GROUP BY 1
ORDER BY 1

-- --------------------------------------------------------
-- QUERY 3: Tick-Level Liquidity Distribution (Snapshot)
-- --------------------------------------------------------
-- This gives you the exact liquidity at each tick for fee share calculation
-- Requires Dune's uniswap_v3_ethereum schema

SELECT 
    tick,
    liquidity_net,
    liquidity_gross,
    price_1_0  -- Price of token1 (ETH) in token0 (USDC)
FROM uniswap_v3_ethereum.ticks
WHERE pool = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
  AND block_time >= NOW() - INTERVAL '1' day
ORDER BY tick

-- --------------------------------------------------------
-- QUERY 4: Specific Position History (if you have a Position NFT ID)
-- --------------------------------------------------------
SELECT 
    block_time,
    liquidity,
    tick_lower,
    tick_upper,
    fee_growth_inside0_last_x128,
    fee_growth_inside1_last_x128,
    tokens_owed0,
    tokens_owed1
FROM uniswap_v3_ethereum.position_snapshots
WHERE position_id = YOUR_POSITION_ID  -- Replace with your NFT token ID
ORDER BY block_time

-- --------------------------------------------------------
-- QUERY 5: Pool Day Data (Alternative to The Graph)
-- --------------------------------------------------------
SELECT 
    date,
    volume_usd,
    fees_usd,
    tvl_usd,
    price,
    liquidity
FROM uniswap_v3_ethereum.pool_day_data
WHERE pool = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
  AND date >= DATE('2024-01-01')
ORDER BY date
