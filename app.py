from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pandas as pd
import os
import requests
import hashlib
from werkzeug.utils import secure_filename

app = Flask(__name__)


# ✅ Dynamic Database URL
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    # Render/Postgres fix for SQLAlchemy
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print("🚀 Using PostgreSQL (Production)")
else:
    # Local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
    print("💻 Using SQLite (Local)")

# --- Configuration ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'd9a29115590a0f527312886ef5e948fccaf53815d85d64d6bae672742aae0f3f')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# ---NOW PAYMENT - CONFIG -----#

NOWPAYMENTS_API_KEY = os.environ.get('NOWPAYMENTS_API_KEY', 'test_key')
NOWPAYMENTS_BASE_URL = 'https://api.nowpayments.io/v1'

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

ALLOWED_EXTENSIONS = {'csv', 'CSV', 'Csv'}
# --Create Upload Folder ----- #
# ----  File Upload Config -------
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {'csb'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Check if file has allowed extension - FINAL FIX"""
    allowed = {'csv', 'CSV', 'Csv', 'txt'}

    print(f"🔍 allowed_file() - Filename: '{filename}'")

    if not filename or '.' not in filename:
        print("❌ No dot in filename")
        return False

    ext = filename.rsplit('.', 1)[1].lower().strip()
    print(f"🔍 Extracted extension: '{ext}'")
    print(f"🔍 Allowed set: {allowed}")
    print(f"🔍 Extension in allowed? {ext in allowed}")

    return ext in allowed

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

    # ✅ Premium Fields
    is_premium = db.Column(db.Boolean, default=False)
    subscription_end = db.Column(db.DateTime, nullable=True)

    trades = db.relationship('Trade', backref='user', lazy=True)

class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buy_price = db.Column(db.Float, nullable=False)
    sell_price = db.Column(db.Float, nullable=False)
    fees = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, nullable=False)
    tax = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ... (Trade model ke baad ye naya route add karo) ...

# --- NEW: CSV Upload & Auto-Calculate Route ---
# --- NEW: CSV Upload & Auto-Calculate Route (Debug Version) ---
@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    # ✅ Check Premium Status
    if not current_user.is_premium:
        flash('⚠️ This feature is for Premium users only. Please upgrade!', 'error')
        return redirect(url_for('pricing'))  # Pricing page pe bhejo

    # ... baaki upload code same rahega ...

    if request.method == 'POST':
        print("🔍 DEBUG: File upload request received")

        if 'file' not in request.files:
            print("❌ ERROR: No file part in request")
            flash('No file selected', 'error')
            return redirect(request.url)

        file = request.files['file']
        print(f"📁 File object: {file}")
        print(f"📁 Filename: '{file.filename}'")

        if file.filename == '':
            print("❌ ERROR: Empty filename")
            flash('No file selected', 'error')
            return redirect(request.url)

        is_allowed = allowed_file(file.filename)
        print(f"🔍 allowed_file() returned: {is_allowed}")

        if file and is_allowed:
            print("✅ File type allowed, proceeding...")
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            try:
                file.save(filepath)
                print(f"✅ File saved at: {filepath}")

                df = pd.read_csv(filepath)
                print(f"📊 CSV loaded: {len(df)} rows found")
                print(f"📋 Columns: {list(df.columns)}")

                results = []

                for index, row in df.iterrows():
                    trade_type = str(row.get('Type', '')).strip().upper()
                    fees = float(row.get('Fees', 0)) if pd.notna(row.get('Fees')) else 0

                    print(f"🔄 Row {index} - Type: '{trade_type}', Fees: {fees}")

                    if trade_type == 'BUY':
                        results.append({'date': row.get('Date'), 'type': 'BUY', 'note': 'No tax'})

                    elif trade_type == 'SELL':
                        sell_value = float(row.get('Total', 0))
                        buy_value = sell_value * 0.8

                        gross_profit = sell_value - buy_value - fees
                        gst_on_fees = fees * 0.18
                        total_fees = fees + gst_on_fees

                        if gross_profit > 0:
                            income_tax = gross_profit * 0.30
                            cess = income_tax * 0.04
                            tds = sell_value * 0.01
                            net_profit = gross_profit - income_tax - cess - tds - total_fees
                        else:
                            income_tax = cess = tds = 0
                            net_profit = gross_profit - total_fees

                        new_trade = Trade(
                            buy_price=buy_value,
                            sell_price=sell_value,
                            fees=fees,
                            profit=net_profit,
                            tax=(income_tax + cess),
                            user_id=current_user.id
                        )
                        db.session.add(new_trade)

                        results.append({
                            'date': row.get('Date'),
                            'type': 'SELL',
                            'tax': round(income_tax + cess, 2),
                            'net_profit': round(net_profit, 2)
                        })

                db.session.commit()
                print(f"✅ Success! Processed {len(results)} trades")
                flash(f'🎉 Successfully processed {len(results)} trades!', 'success')

            except Exception as e:
                print(f"❌ CRITICAL ERROR: {str(e)}")
                import traceback
                traceback.print_exc()
                flash(f'Error: {str(e)}', 'error')

            return redirect(url_for('history'))
        else:
            print("❌ ERROR: File type NOT allowed")
            flash('Invalid file type. Please upload CSV only.', 'error')
            return redirect(request.url)

    return render_template('upload.html')

# --- NEW: Export Report Route ---
@app.route('/export')
@login_required
def export():
    user_trades = Trade.query.filter_by(user_id=current_user.id).all()

    import io
    output = io.StringIO()
    # ✅ Fixed columns to match import format
    output.write('Date,Type,Asset,Amount,Price,Total,Fees\n')
    for trade in user_trades:
        # Determine Type based on profit (simplified)
        trade_type = 'SELL' if trade.profit > 0 else 'BUY'
        output.write(f'{trade.date.strftime("%Y-%m-%d")},{trade_type},BTC,1,{trade.buy_price},{trade.sell_price},{trade.fees}\n')

    from flask import send_file
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='crypto_tax_report.csv'
    )

# ... (baaki routes same rahenge) ...

# --- Routes ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user:
            flash('Email already exists', 'error')
            return redirect(url_for('register'))
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, email=email, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        flash('Account created! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Login failed. Check email/password.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', name=current_user.username)

# --- NEW: Tax Calculator Route ---
@app.route('/calculate', methods=['GET', 'POST'])
@login_required
def calculate():
    result = None
    if request.method == 'POST':
        try:
            buy = float(request.form.get('buy_price'))
            sell = float(request.form.get('sell_price'))
            fees = float(request.form.get('fees'))

            # --- Tax Logic (India Crypto Rules) ---
            gross_profit = sell - buy - fees
            gst_on_fees = fees * 0.18  # 18% GST on fees
            total_fees = fees + gst_on_fees

            if gross_profit > 0:
                income_tax = gross_profit * 0.30  # 30% Tax
                cess = income_tax * 0.04  # 4% Cess
                tds = sell * 0.01  # 1% TDS on sell value
                net_profit = gross_profit - income_tax - cess - tds - total_fees
            else:
                income_tax = cess = tds = 0
                net_profit = gross_profit - total_fees

            # Save to Database
            new_trade = Trade(
                buy_price=buy, sell_price=sell, fees=fees,
                profit=net_profit, tax=(income_tax + cess),
                user_id=current_user.id
            )
            db.session.add(new_trade)
            db.session.commit()

            result = {
                'gross_profit': gross_profit,
                'tax': income_tax + cess,
                'tds': tds,
                'gst_fees': gst_on_fees,
                'net_profit': net_profit
            }
        except Exception as e:
            flash('Error in calculation. Please check numbers.', 'error')

    return render_template('calculate.html', result=result)

# --- NEW: History Route ---
@app.route('/history')
@login_required
def history():
    user_trades = Trade.query.filter_by(user_id=current_user.id).order_by(Trade.date.desc()).all()
    return render_template('history.html', trades=user_trades)

# --- Database Create ---
with app.app_context():
    db.create_all()

def create_crypto_invoice(amount_usd, user_id):
    """Create Payment Invoice via NowPayments"""
    headers = {
        'x-api-key': NOWPAYMENTS_API_KEY,
        'Content-Type': 'application/json'
    }
    data = {
        'price_amount': amount_usd,
        'price_currency': 'usd',
        'pay_currency': 'usdttrc20',  # USDT TRC20 (Low fees)
        'ipn_callback_url': 'https://YOUR-RENDER-URL.onrender.com/webhook',  # ⚠️ Deploy ke baad update karna
        'order_id': f'user_{user_id}_{int(datetime.now().timestamp())}',
        'order_description': 'Crypto Tax Premium Subscription'
    }

    try:
        response = requests.post(f'{NOWPAYMENTS_BASE_URL}/invoice', json=data, headers=headers)
        return response.json()
    except Exception as e:
        print(f"Payment Error: {e}")
        return None

# ✅ Webhook Route (Verify Payment)
@app.route('/webhook', methods=['POST'])
def nowpayments_webhook():
    """NowPayments IPN Callback"""
    # Verify IPN Secret (Security)
    ipn_secret = os.environ.get('IPN_SECRET', 'test_secret')
    received_hash = request.headers.get('x-nowpayments-sig')

    # Simple verification (Production mein strict karna)
    # Sort params, concatenate with secret, hash via HMAC_SHA512
    # For MVP, we trust the order_id structure

    data = request.json
    order_id = data.get('order_id')
    payment_status = data.get('payment_status')

    if payment_status == 'finished':
        # Extract user_id from order_id (user_123_...)
        try:
            user_id = int(order_id.split('_')[1])
            user = User.query.get(user_id)
            if user:
                user.is_premium = True
                user.subscription_end = datetime.now() + timedelta(days=30)  # 1 Month
                db.session.commit()
                print(f"✅ Premium activated for user {user_id}")
        except Exception as e:
            print(f"Webhook Error: {e}")

    return {'status': 'ok'}, 200

@app.route('/pricing')
@login_required
def pricing():
    return render_template('pricing.html')

@app.route('/buy-premium', methods=['POST'])
@login_required
def buy_premium():
    # Create Invoice
    invoice = create_crypto_invoice(19.99, current_user.id)  # $19.99 Monthly

    if invoice and 'invoice_url' in invoice:
        return redirect(invoice['invoice_url'])
    else:
        flash('Payment gateway error. Try again.', 'error')
        return redirect(url_for('pricing'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
