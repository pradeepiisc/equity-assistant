import yfinance as yf
import pandas as pd

# Try DCAL.NS with different period settings
ticker = 'DCAL.NS'
stock = yf.Ticker(ticker)

print('Fetching', ticker, 'historical data...')

# Try different period approaches
approaches = [
    ('1y', '1 year period'),
    ('2y', '2 year period'), 
    ('max', 'Maximum available')
]

for period, desc in approaches:
    try:
        print('\n' + desc + ' (' + period + '):')
        df = stock.history(period=period)
        
        if not df.empty:
            print('  Data points:', len(df))
            print('  Date range:', df.index.min().date(), 'to', df.index.max().date())
            
            # Calculate DMAs if we have enough data
            if len(df) >= 200:
                df['SMA_50'] = df['Close'].rolling(window=50).mean()
                df['SMA_200'] = df['Close'].rolling(window=200).mean()
                
                latest = df.iloc[-1]
                print('  Current Price: ₹' + str(round(latest['Close'], 2)))
                print('  50 DMA: ₹' + str(round(latest['SMA_50'], 2)))
                print('  200 DMA: ₹' + str(round(latest['SMA_200'], 2)))
                
                # Check if these match your expected values
                if abs(latest['SMA_50'] - 204) < 10:
                    print('  ✓ 50 DMA close to expected ~204!')
                if abs(latest['SMA_200'] - 234) < 10:
                    print('  ✓ 200 DMA close to expected ~234!')
            else:
                print('  Insufficient data for DMA calculation')
        else:
            print('  No data returned')
            
    except Exception as e:
        print('  Error:', e)
