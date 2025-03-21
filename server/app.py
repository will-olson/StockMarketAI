from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import logging
import traceback
from dotenv import load_dotenv
import numpy as np
import pandas as pd
import glob

# Import the AdvancedStatisticalAnalyzer directly
from statistical_analysis import AdvancedStatisticalAnalyzer

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

def load_financial_data(file_path):
    """
    Load and parse financial data from JSON file
    
    :param file_path: Path to the JSON file containing financial metrics
    :return: Parsed financial data as a DataFrame
    """
    try:
        # Load JSON data
        with open(file_path, 'r') as f:
            json_data = json.load(f)
        
        # Convert nested dictionary to DataFrame
        import pandas as pd
        import re
        
        # Predefined columns with mapping
        column_mapping = {
            'PE Ratio (TTM)': 'P/E Ratio',
            'EPS (TTM)': 'EPS',
            'Market Cap': 'Market Cap',
            'Previous Close': 'Current Price',
            'Beta': 'Beta',
            'Volume': 'Volume'
        }
        
        # Convert nested dictionary to list of dictionaries
        df_data = []
        for ticker, stock_data in json_data.items():
            stock_entry = {
                'Ticker': ticker 
            }
            
            # Helper function to clean numeric values
            def clean_numeric_value(value):
                if not value or value == '--' or value == 'N/A':
                    return None
                
                # Remove commas and handle percentage/multiplier suffixes
                value = str(value).replace(',', '').replace('%', '')
                
                # Handle market cap and other suffixes
                multipliers = {
                    'B': 1_000_000_000,    # Billions
                    'M': 1_000_000,        # Millions
                    'T': 1_000_000_000_000 # Trillions
                }
                
                # Check for multiplier suffix
                if value and value[-1] in multipliers:
                    try:
                        return float(value[:-1]) * multipliers[value[-1]]
                    except ValueError:
                        return None
                
                # Attempt direct conversion
                try:
                    return float(value)
                except ValueError:
                    return None
            
            # Predefined columns to extract
            expected_columns = [
                'Market Cap', 'P/E Ratio', 'EPS', 'Dividend Yield', 
                'Current Price', 'Beta', 'Volume', 'Momentum Score'
            ]
            
            # Map and clean columns
            for original_col, mapped_col in column_mapping.items():
                if original_col in stock_data:
                    stock_entry[mapped_col] = clean_numeric_value(stock_data[original_col])
            
            # Robust Dividend Yield Extraction
            if 'Forward Dividend & Yield' in stock_data:
                try:
                    dividend_yield = stock_data['Forward Dividend & Yield']
                    
                    # Multiple extraction strategies
                    extraction_strategies = [
                        # Strategy 1: Extract percentage in parentheses
                        lambda x: float(re.search(r'$$(\d+\.\d+)%$$', x).group(1)) if re.search(r'$$(\d+\.\d+)%$$', x) else None,
                        
                        # Strategy 2: Extract first number followed by %
                        lambda x: float(re.search(r'(\d+\.\d+)%', x).group(1)) if re.search(r'(\d+\.\d+)%', x) else None,
                        
                        # Strategy 3: Split and take second part
                        lambda x: float(x.split()[1].replace('(', '').replace(')', '').replace('%', '')) if len(x.split()) > 1 else None
                    ]
                    
                    # Try each strategy until successful
                    for strategy in extraction_strategies:
                        try:
                            dividend_yield_value = strategy(dividend_yield)
                            if dividend_yield_value is not None:
                                stock_entry['Dividend Yield'] = dividend_yield_value
                                break
                        except Exception:
                            continue
                    else:
                        # If no strategy works, set to None
                        stock_entry['Dividend Yield'] = None
                
                except Exception as e:
                    logger.warning(f"Could not process dividend yield: {e}")
                    stock_entry['Dividend Yield'] = None
            
            # Handle 52 Week Range for Momentum Score
            if '52 Week Range' in stock_data:
                try:
                    low, high = stock_data['52 Week Range'].split(' - ')
                    stock_entry['52 Week Low'] = clean_numeric_value(low)
                    stock_entry['52 Week High'] = clean_numeric_value(high)
                    
                    # Calculate Momentum Score if possible
                    if (stock_entry.get('Current Price') is not None and 
                        stock_entry.get('52 Week Low') is not None and 
                        stock_entry.get('52 Week High') is not None):
                        current_price = stock_entry['Current Price']
                        week_low = stock_entry['52 Week Low']
                        week_high = stock_entry['52 Week High']
                        
                        stock_entry['Momentum Score'] = (current_price - week_low) / (week_high - week_low) if week_high != week_low else 0
                except:
                    pass
            
            # Ensure all expected columns exist
            for col in expected_columns:
                if col not in stock_entry:
                    stock_entry[col] = None
            
            # Additional explicit handling for Volume and Beta
            if 'Avg. Volume' in stock_data:
                stock_entry['Volume'] = clean_numeric_value(stock_data['Avg. Volume'])
            
            df_data.append(stock_entry)
        
        # Create DataFrame
        df = pd.DataFrame(df_data)
        
        # Debugging print
        print("DataFrame Columns:", df.columns.tolist())
        print("\nColumn Value Counts:")
        for col in expected_columns:
            print(f"{col} - Non-Null Count: {df[col].count()}")
        
        return df
    
    except Exception as e:
        logger.error(f"Error loading financial data: {e}")
        logger.error(traceback.format_exc())
        raise

@app.route('/api/financial-analysis', methods=['POST'])
def perform_financial_analysis():
    """
    Perform financial analysis on a specific dataset
    """
    try:
        # Get request data
        data = request.json
        data_source = data.get('data_source')
        analysis_type = data.get('type', 'comprehensive')
        
        # Validate data source
        if not data_source:
            return jsonify({
                'error': 'No data source provided'
            }), 400
        
        # Potential search paths
        search_paths = [
            os.path.join(os.getcwd(), 'server', data_source),
            os.path.join(os.getcwd(), data_source),
            os.path.join(os.getcwd(), 'data', data_source),
            os.path.join(os.getcwd(), 'server', 'data', data_source)
        ]
        
        # Find the first existing file
        file_path = None
        for path in search_paths:
            logger.info(f"Checking path: {path}")
            if os.path.exists(path):
                file_path = path
                break
        
        # Raise error if no file found
        if not file_path:
            logger.error(f"Could not find file: {data_source}")
            return jsonify({
                'error': 'File not found',
                'message': f'Could not locate {data_source}',
                'searched_paths': search_paths
            }), 404
        
        # Log found file path
        logger.info(f"Found file at: {file_path}")
        
        # Load financial data
        df = load_financial_data(file_path)
        
        # Check OpenAI API key
        openai_api_key = os.getenv('OPENAI_API_KEY')
        if not openai_api_key:
            logger.error("OpenAI API key is not set")
            return jsonify({
                'error': 'OpenAI API key is not configured',
                'message': 'Please set the OPENAI_API_KEY environment variable'
            }), 500
        
        # Initialize analyzer using the method from statistical_analysis.py
        analyzer = AdvancedStatisticalAnalyzer(df, openai_api_key=openai_api_key)
        
        # Perform analysis based on type
        try:
            if analysis_type == 'comprehensive':
                # Generate comprehensive report
                results = analyzer.generate_comprehensive_report()
                
                # Generate visualizations
                analyzer.generate_basic_visualizations()
                
                # Generate advanced AI insights
                ai_insights = analyzer.generate_advanced_analysis()
                
                return jsonify({
                    'report': results,
                    'ai_insights': ai_insights,
                    'visualizations': _get_visualization_paths()
                })
            
            elif analysis_type == 'descriptive':
                # Generate descriptive statistics
                results = analyzer.descriptive_statistics()
                return jsonify(results.to_dict(orient='records'))
            
            elif analysis_type == 'advanced':
                # Generate advanced analysis
                results = analyzer.generate_advanced_analysis()
                return jsonify(results)
            
            else:
                raise ValueError(f"Unsupported analysis type: {analysis_type}")
        
        except Exception as analysis_error:
            logger.error(f"Analysis generation error: {analysis_error}")
            logger.error(traceback.format_exc())
            return jsonify({
                'error': 'Failed to generate analysis',
                'message': str(analysis_error),
                'traceback': traceback.format_exc()
            }), 500
    
    except Exception as e:
        logger.error(f"Financial analysis route error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Analysis failed',
            'message': str(e),
            'traceback': traceback.format_exc()
        }), 500

def _get_visualization_paths():
    """
    Retrieve paths to generated visualizations
    
    :return: Dictionary of visualization file paths
    """
    base_path = 'financial_analysis_output/charts'
    return {
        'distribution_plot': os.path.join(base_path, 'distribution_plots.png'),
        'boxplot': os.path.join(base_path, 'boxplot_metrics.png'),
        'correlation_heatmap': os.path.join(base_path, 'correlation_heatmap.png'),
        'market_cap_eps_bubble': os.path.join(base_path, 'market_cap_eps_bubble.png')
    }

@app.route('/api/visualizations/<filename>', methods=['GET'])
def get_visualization(filename):
    """
    Serve visualization files
    """
    try:
        file_path = os.path.join('financial_analysis_output/charts', filename)
        
        if not os.path.exists(file_path):
            return jsonify({
                'error': 'Visualization not found',
                'message': f'Could not find visualization: {filename}'
            }), 404
        
        return send_file(file_path, mimetype='image/png')
    
    except Exception as e:
        logger.error(f"Visualization retrieval error: {e}")
        return jsonify({
            'error': 'Could not retrieve visualization',
            'message': str(e)
        }), 500

@app.route('/api/download/<report_type>', methods=['GET'])
def download_report(report_type):
    """
    Download various report types
    """
    try:
        # Map report types to file paths
        report_paths = {
            'excel': 'financial_analysis_output/comprehensive_financial_analysis.xlsx',
            'insights': 'advanced_financial_insights.txt',
            'summary': 'financial_analysis_output/summary_report.json'
        }
        
        file_path = report_paths.get(report_type)
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({
                'error': 'Report not found',
                'message': f'Could not find report: {report_type}'
            }), 404
        
        return send_file(file_path, as_attachment=True)
    
    except Exception as e:
        logger.error(f"Report download error: {e}")
        return jsonify({
            'error': 'Could not download report',
            'message': str(e)
        }), 500
    
@app.route('/api/financial-analysis/visual-insights', methods=['POST'])
def generate_visual_insights():
    """
    Generate visual insights for the most recently used dataset
    """
    try:
        # Get the server directory path
        server_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Find the most recent JSON file
        most_recent_file = None
        most_recent_time = 0
        
        logger.info(f"Searching in server directory: {server_dir}")
        
        try:
            # List files in the server directory
            for filename in os.listdir(server_dir):
                if filename.startswith('financial_metrics') and filename.endswith('.json'):
                    full_path = os.path.join(server_dir, filename)
                    file_time = os.path.getmtime(full_path)
                    
                    if file_time > most_recent_time:
                        most_recent_time = file_time
                        most_recent_file = full_path
        except Exception as e:
            logger.error(f"Error searching server directory: {e}")
            return jsonify({
                'error': 'Failed to search directory',
                'message': str(e)
            }), 500
        
        # If no file found, return error
        if not most_recent_file:
            # Log files in the directory for debugging
            logger.warning("Files in server directory:")
            for filename in os.listdir(server_dir):
                logger.warning(filename)
            
            return jsonify({
                'error': 'No financial datasets found',
                'message': 'Please upload a financial metrics JSON file first',
                'server_directory': server_dir
            }), 404
        
        logger.info(f"Found most recent file: {most_recent_file}")
        
        # Load the dataset
        df = load_financial_data(most_recent_file)
        
        # Initialize analyzer
        openai_api_key = os.getenv('OPENAI_API_KEY')
        analyzer = AdvancedStatisticalAnalyzer(df, openai_api_key)
        
        # Prepare distribution data
        distribution_data = []
        metrics = ['Market Cap', 'P/E Ratio', 'EPS', 'Dividend Yield', 'Beta']
        
        for metric in metrics:
            data = df[metric].dropna()
            
            distribution_data.append({
                'metric': metric,
                'mean': float(np.mean(data)) if len(data) > 0 else 0,
                'median': float(np.median(data)) if len(data) > 0 else 0,
                'stdDev': float(np.std(data)) if len(data) > 0 else 0
            })
        
        # Prepare stock type data
        def classify_stock_type(row):
            if pd.isna(row['Beta']) or pd.isna(row['Market Cap']):
                return 'Unclassified'
            
            if row['Market Cap'] > 100_000_000_000:
                return 'Mega Cap'
            elif pd.notna(row['Dividend Yield']) and row['Dividend Yield'] > 3:
                return 'High Yield'
            elif row['Beta'] > 1.5:
                return 'High Volatility'
            elif pd.notna(row['EPS']) and row['EPS'] < 0:
                return 'Speculative'
            else:
                return 'Stable'
        
        df['Stock Type'] = df.apply(classify_stock_type, axis=1)
        stock_type_data = df['Stock Type'].value_counts()
        
        stock_type_distribution = [
            {"name": str(stock_type), "value": int(count)}
            for stock_type, count in stock_type_data.items()
        ]
        
        # Prepare market cap tiers
        df['Market Cap Tier'] = pd.qcut(
            df['Market Cap'], 
            q=[0, 0.2, 0.4, 0.6, 0.8, 1.0], 
            labels=['Very Small', 'Small', 'Medium', 'Large', 'Very Large']
        )
        market_cap_tiers = df['Market Cap Tier'].value_counts()
        
        market_cap_data = [
            {"tier": str(tier), "count": int(count)}
            for tier, count in market_cap_tiers.items()
        ]
        
        return jsonify({
            'distributionData': distribution_data,
            'stockTypeData': stock_type_distribution,
            'marketCapData': market_cap_data
        }), 200
    
    except Exception as e:
        logger.error(f"Visual insights generation error: {e}")
        return jsonify({
            'error': 'Failed to generate visual insights',
            'message': str(e)
        }), 500

@app.route('/api/financial-datasets', methods=['GET'])
def list_financial_datasets():
    """
    List available financial datasets
    """
    try:
        # Get current working directory
        current_dir = os.getcwd()
        logger.info(f"Current working directory: {current_dir}")

        # Potential directories to search
        search_paths = [
            current_dir,
            os.path.join(current_dir, 'server'),
            os.path.join(current_dir, 'data'),
            os.path.join(current_dir, 'server', 'data')
        ]

        # Collect all datasets
        datasets = []

        # Search through potential paths
        for search_path in search_paths:
            logger.info(f"Checking directory: {search_path}")
            
            try:
                # List files in the directory
                all_files = os.listdir(search_path)
                logger.info(f"Files in {search_path}: {all_files}")

                # Find JSON files starting with financial_metrics
                path_datasets = [
                    f for f in all_files 
                    if f.endswith('.json') and f.startswith('financial_metrics')
                ]

                # Add found datasets with full path
                datasets.extend([
                    os.path.join(search_path, dataset) for dataset in path_datasets
                ])
            
            except FileNotFoundError:
                logger.warning(f"Directory not found: {search_path}")
            except Exception as dir_error:
                logger.error(f"Error searching {search_path}: {dir_error}")

        # Log final datasets
        logger.info(f"Detected datasets: {datasets}")

        # Return just the filenames, not full paths
        dataset_names = [os.path.basename(dataset) for dataset in datasets]

        return jsonify(dataset_names)
    
    except Exception as e:
        logger.error(f"Error listing datasets: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Could not list datasets',
            'message': str(e)
        }), 500

@app.route('/api/statistical-analysis/advanced-metrics', methods=['POST'])
def advanced_statistical_analysis():
    try:
        # Find the most recent JSON file in the server directory
        server_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Search for financial metrics JSON files
        json_files = glob.glob(os.path.join(server_dir, 'financial_metrics*.json'))
        
        # If no files found, search in data subdirectory
        if not json_files:
            json_files = glob.glob(os.path.join(server_dir, 'data', 'financial_metrics*.json'))
        
        # If still no files found, raise an error
        if not json_files:
            logger.error("No financial metrics JSON files found")
            return jsonify({
                'error': 'No financial datasets found',
                'message': 'Please upload a financial metrics JSON file first'
            }), 404
        
        # Get the most recently modified file
        most_recent_file = max(json_files, key=os.path.getmtime)
        
        logger.info(f"Using most recent file: {most_recent_file}")
        
        # Load the dataset
        df = load_financial_data(most_recent_file)
        
        # Custom statistical functions
        def calculate_mean(data):
            return sum(data) / len(data) if data else None
        
        def calculate_median(data):
            sorted_data = sorted(data)
            n = len(sorted_data)
            mid = n // 2
            return (sorted_data[mid] + sorted_data[~mid]) / 2 if n else None
        
        def calculate_standard_deviation(data):
            if not data:
                return None
            mean = calculate_mean(data)
            variance = sum((x - mean) ** 2 for x in data) / len(data)
            return variance ** 0.5
        
        def calculate_skewness(data):
            if not data:
                return None
            mean = calculate_mean(data)
            std_dev = calculate_standard_deviation(data)
            if std_dev == 0:
                return 0
            return sum(((x - mean) / std_dev) ** 3 for x in data) / len(data)
        
        def calculate_kurtosis(data):
            if not data:
                return None
            mean = calculate_mean(data)
            std_dev = calculate_standard_deviation(data)
            if std_dev == 0:
                return 0
            return sum(((x - mean) / std_dev) ** 4 for x in data) / len(data) - 3
        
        def calculate_correlation(x, y):
            if len(x) != len(y):
                return None
            
            mean_x = calculate_mean(x)
            mean_y = calculate_mean(y)
            
            numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
            denominator = (
                sum((xi - mean_x) ** 2 for xi in x) * 
                sum((yi - mean_y) ** 2 for yi in y)
            ) ** 0.5
            
            return numerator / denominator if denominator != 0 else 0
        
        # Statistical Summary
        numeric_columns = ['Market Cap', 'P/E Ratio', 'EPS', 'Dividend Yield', 'Beta']
        statistical_summary = []
        
        for column in numeric_columns:
            data = df[column].dropna().tolist()
            
            statistical_summary.append({
                'name': column,
                'mean': float(calculate_mean(data)) if data else None,
                'median': float(calculate_median(data)) if data else None,
                'stdDev': float(calculate_standard_deviation(data)) if data else None,
                'skewness': float(calculate_skewness(data)) if data else None,
                'kurtosis': float(calculate_kurtosis(data)) if data else None
            })
        
        # Correlation Analysis
        correlation_tests = []
        for i in range(len(numeric_columns)):
            for j in range(i+1, len(numeric_columns)):
                col1, col2 = numeric_columns[i], numeric_columns[j]
                data1 = df[col1].dropna().tolist()
                data2 = df[col2].dropna().tolist()
                
                # Ensure same length
                min_length = min(len(data1), len(data2))
                data1 = data1[:min_length]
                data2 = data2[:min_length]
                
                correlation = calculate_correlation(data1, data2)
                
                correlation_tests.append({
                    'metricPair': f'{col1} vs {col2}',
                    'correlation': float(correlation) if correlation is not None else None
                })
        
        # Simple Regression Analysis (Market Cap vs P/E Ratio)
        market_cap_data = df['Market Cap'].dropna().tolist()
        pe_ratio_data = df['P/E Ratio'].dropna().tolist()
        
        # Ensure same length
        min_length = min(len(market_cap_data), len(pe_ratio_data))
        market_cap_data = market_cap_data[:min_length]
        pe_ratio_data = pe_ratio_data[:min_length]
        
        # Simple linear regression approximation
        def simple_linear_regression(x, y):
            n = len(x)
            
            # Calculate means
            mean_x = calculate_mean(x)
            mean_y = calculate_mean(y)
            
            # Calculate slope
            numerator = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
            denominator = sum((xi - mean_x) ** 2 for xi in x)
            
            slope = numerator / denominator if denominator != 0 else 0
            
            # Calculate intercept
            intercept = mean_y - slope * mean_x
            
            return slope, intercept
        
        slope, intercept = simple_linear_regression(market_cap_data, pe_ratio_data)
        
        regression_data = [
            {'independentVar': x, 'dependentVar': y} 
            for x, y in zip(market_cap_data, pe_ratio_data)
        ]
        
        # Time Series Analysis (Momentum Score)
        try:
            momentum_data = df['Momentum Score'].dropna().tolist()
            
            # Simple time series representation
            time_series_analysis = {
                'metric': 'Momentum Score',
                'data': [
                    {'period': str(i), 'value': float(value)} 
                    for i, value in enumerate(momentum_data)
                ]
            }
        except Exception as ts_error:
            logger.warning(f"Time series analysis failed: {ts_error}")
            time_series_analysis = {
                'metric': 'Momentum Score',
                'data': []
            }
        
        return jsonify({
            'statisticalSummary': statistical_summary,
            'correlationAnalysis': correlation_tests,
            'regressionAnalysis': {
                'independentVariable': 'Market Cap',
                'dependentVariable': 'P/E Ratio',
                'data': regression_data,
                'slope': float(slope),
                'intercept': float(intercept)
            },
            'timeSeriesAnalysis': time_series_analysis
        }), 200
    
    except Exception as e:
        logger.error(f"Statistical analysis error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': 'Failed to generate statistical analysis',
            'message': str(e),
            'traceback': traceback.format_exc()
        }), 500

# Main Execution
if __name__ == '__main__':
    # Ensure output directories exist
    os.makedirs('financial_analysis_output/charts', exist_ok=True)
    
    # Run the Flask app
    app.run(debug=True, port=5000)

