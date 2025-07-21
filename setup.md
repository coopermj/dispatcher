# Dispatch PDF Converter - Setup Guide

## 🚀 Quick Start

### 1. Clone and Setup Environment

```bash
# Clone or download the project
cd dispatch_converter

# Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install
```

### 2. Configure Environment Variables

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env file with your preferences
nano .env  # or use your preferred editor
```

### 3. Setup Google OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Gmail API
4. Create OAuth 2.0 credentials (Desktop application)
5. Download the credentials file as `credentials.json`
6. Place it in the project root directory

### 4. Setup ReMarkable (Optional)

```bash
# Install rmapi
# For macOS with Homebrew:
brew install rmapi

# For other systems, download from:
# https://github.com/juruen/rmapi/releases

# Authenticate with your ReMarkable
rmapi
# Follow the authentication prompts
```

### 5. Run the Converter

```bash
python main.py
```

## ⚙️ Configuration Options

### Core Settings (.env file)

```bash
# Email processing
MAX_EMAILS=5                      # Number of emails to process
FORCE_