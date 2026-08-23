.PHONY: app demo-cli download tests clean

# Streamlit UI (default target)
app:
	.venv/bin/streamlit run app.py

# Headless swap_level backtest on the synthetic demo tape
demo-cli:
	.venv/bin/python run_claude.py --demo --demo-days 25 --sweep --out run1

# Download a range of Binance klines (5 MB/month) into data/
download:
	.venv/bin/python run_claude.py --download 2024-01 2024-06

tests:
	.venv/bin/python -m pytest tests/ -v

clean:
	rm -rf __pycache__ .pytest_cache
