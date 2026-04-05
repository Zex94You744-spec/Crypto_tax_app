from flask import Flask, render_template, request, redirect, url_for, flash, send_file, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import io
import requests
import hmac
import hashlib

# --- Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', '4a7fec2b1365bf12f01068e59c0679bc5989076b939eece35885f373f00fe0c5')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

# ✅ Dynamic Database (SQLite for Local, PostgreSQL for Render)
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print("🚀 Using PostgreSQL (Production)")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
    print("💻 Using SQLite (Local)")

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ✅ Global Allowed Extensions
ALLOWED_EXTENSIONS = {'csv', 'CSV', 'Csv', 'txt'}

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    # ✅ Premium Fields
    is_premium = db.Column(db.Boolean, default=False)
    subscription_end = db.Column(db.DateTime, nullable=True)
    plan_type = db.Column(db.String(50), default='free')  # free, monthly, yearly
    # ✅ Free User Limits
    free_calculations_count = db.Column(db.Integer, default=0)
    free_calculations_reset = db.Column(db.DateTime, default=datetime.utcnow)
    trades = db.relationship('Trade', backref='user', lazy=True, cascade='all, delete-orphan')

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

# --- Helper Functions ---
def allowed_file(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower().strip()
    return ext in ALLOWED_EXTENSIONS

def check_premium():
    """Check if user has active premium subscription"""
    if not current_user.is_premium:
        return False
    if current_user.subscription_end and current_user.subscription_end < datetime.now():
        current_user.is_premium = False
        current_user.plan_type = 'free'
        current_user.free_calculations_count = 0
        current_user.free_calculations_reset = datetime.utcnow()
        db.session.commit()
        return False
    return True

def check_free_limit():
    """Check if free user has reached calculation limit (5/month)"""
    if current_user.is_premium:
        return True, 0

    # Reset counter if month changed
    if current_user.free_calculations_reset < datetime.utcnow() - timedelta(days=30):
        current_user.free_calculations_count = 0
        current_user.free_calculations_reset = datetime.utcnow()
        db.session.commit()

    remaining = 5 - current_user.free_calculations_count
    return current_user.free_calculations_count < 5, remaining

def increment_free_calc():
    """Increment free calculation counter"""
    if not current_user.is_premium:
        current_user.free_calculations_count += 1
        db.session.commit()

def create_crypto_invoice(amount_usd, user_id, plan):
    """Create Payment Invoice via NowPayments - Auto Convert to USDT TRC20"""
    api_key = os.environ.get('NOWPAYMENTS_API_KEY')
    if not api_key:
        print("❌ NowPayments API Key not configured")
        return None

    headers = {
        'x-api-key': api_key,
        'Content-Type': 'application/json'
    }

    # Get live URL from environment or request
    live_url = os.environ.get('RENDER_EXTERNAL_URL', request.host_url.rstrip('/'))

    data = {
        'price_amount': amount_usd,
        'price_currency': 'usd',
        'pay_currency': 'usdttrc20',  # ✅ Auto-convert to USDT TRC20
        'ipn_callback_url': f'{live_url}/webhook',
        'order_id': f'user_{user_id}_{plan}_{int(datetime.now().timestamp())}',
        'order_description': f'Crypto Tax {plan.title()} Subscription'
    }

    try:
        response = requests.post('https://api.nowpayments.io/v1/invoice', json=data, headers=headers)
        result = response.json()
        print(f"📝 Invoice Created: {result}")
        return result
    except Exception as e:
        print(f"Payment Error: {e}")
        return None

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
    is_active = check_premium()
    total_trades = Trade.query.filter_by(user_id=current_user.id).count()
    total_profit = db.session.query(db.func.sum(Trade.profit)).filter_by(user_id=current_user.id).scalar() or 0
    total_tax = db.session.query(db.func.sum(Trade.tax)).filter_by(user_id=current_user.id).scalar() or 0
    has_limit, remaining = check_free_limit()
    return render_template('dashboard.html', name=current_user.username, is_active=is_active, 
                         total_trades=total_trades, total_profit=total_profit, total_tax=total_tax,
                         free_remaining=remaining, free_limit=5)

# --- Manual Calculator (FREE with Limit) ---
@app.route('/calculate', methods=['GET', 'POST'])
@login_required
def calculate():
    result = None
    has_limit, remaining = check_free_limit()

    if not has_limit:
        flash('⚠️ Free limit reached (5 calculations/month). Upgrade to Premium!', 'error')
        return redirect(url_for('pricing'))

    if request.method == 'POST':
        try:
            buy = float(request.form.get('buy_price'))
            sell = float(request.form.get('sell_price'))
            fees = float(request.form.get('fees'))
            gross_profit = sell - buy - fees
            gst_on_fees = fees * 0.18
            total_fees = fees + gst_on_fees
            if gross_profit > 0:
                income_tax = gross_profit * 0.30
                cess = income_tax * 0.04
                tds = sell * 0.01
                net_profit = gross_profit - income_tax - cess - tds - total_fees
            else:
                income_tax = cess = tds = 0
                net_profit = gross_profit - total_fees

            new_trade = Trade(buy_price=buy, sell_price=sell, fees=fees, profit=net_profit, 
                            tax=(income_tax + cess), user_id=current_user.id)
            db.session.add(new_trade)
            db.session.commit()
            increment_free_calc()

            result = {'gross_profit': gross_profit, 'tax': income_tax + cess, 'tds': tds, 
                     'gst_fees': gst_on_fees, 'net_profit': net_profit}
            flash('✅ Trade saved successfully!', 'success')
        except Exception as e:
            flash(f'Error in calculation: {str(e)}', 'error')

    return render_template('calculate.html', result=result, remaining=remaining, free_limit=5)

# --- History with Delete (FREE) ---
@app.route('/history')
@login_required
def history():
    # ✅ Force fresh query from database
    db.session.expire_all()
    user_trades = Trade.query.filter_by(user_id=current_user.id).order_by(Trade.date.desc()).all()
    print(f"📊 History: Found {len(user_trades)} trades for user {current_user.id}")

    response = make_response(render_template('history.html', trades=user_trades))
    # ✅ Prevent browser caching
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/delete-trade/<int:trade_id>')
@login_required
def delete_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    if trade.user_id != current_user.id:
        flash('Unauthorized access', 'error')
        return redirect(url_for('history'))
    db.session.delete(trade)
    db.session.commit()
    flash('Trade deleted successfully', 'success')
    return redirect(url_for('history'))

# --- Export All Trades (PREMIUM) ---
@app.route('/export')
@login_required
def export():
    if not check_premium():
        flash('⚠️ Export is for Premium users only.', 'error')
        return redirect(url_for('pricing'))
    user_trades = Trade.query.filter_by(user_id=current_user.id).all()
    output = io.StringIO()
    output.write('Date,Type,Asset,Amount,Price,Total,Fees,Profit,Tax\n')
    for trade in user_trades:
        trade_type = 'SELL' if trade.profit > 0 else 'BUY'
        output.write(f'{trade.date.strftime("%Y-%m-%d")},{trade_type},BTC,1,{trade.buy_price},{trade.sell_price},{trade.fees},{trade.profit},{trade.tax}\n')
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', 
                    as_attachment=True, download_name='crypto_tax_report.csv')

# --- Export Individual Trade (PREMIUM) ---
@app.route('/export-trade/<int:trade_id>')
@login_required
def export_trade(trade_id):
    if not check_premium():
        flash('⚠️ Export is for Premium users only.', 'error')
        return redirect(url_for('pricing'))

    trade = Trade.query.get_or_404(trade_id)
    if trade.user_id != current_user.id:
        flash('Unauthorized access', 'error')
        return redirect(url_for('history'))

    output = io.StringIO()
    output.write('Field,Value\n')
    output.write(f'Date,{trade.date.strftime("%Y-%m-%d")}\n')
    output.write(f'Type,{"SELL" if trade.profit > 0 else "BUY"}\n')
    output.write(f'Buy Price,{trade.buy_price}\n')
    output.write(f'Sell Price,{trade.sell_price}\n')
    output.write(f'Fees,{trade.fees}\n')
    output.write(f'Profit,{trade.profit}\n')
    output.write(f'Tax,{trade.tax}\n')
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', 
                    as_attachment=True, download_name=f'trade_{trade_id}_report.csv')

# --- Pricing Page ---
@app.route('/pricing')
@login_required
def pricing():
    is_active = check_premium()
    has_limit, remaining = check_free_limit()
    return render_template('pricing.html', is_active=is_active, free_remaining=remaining, free_limit=5)

# --- Buy Premium (Local Mock + Production Real) ---
@app.route('/buy-premium', methods=['POST'])
@login_required
def buy_premium():
    plan = request.form.get('plan', 'monthly')

    # ✅ Check if running on Render (Production)
    if os.environ.get('DATABASE_URL') and os.environ.get('NOWPAYMENTS_API_KEY'):
        # PRODUCTION: Real NowPayments
        amount = 99.99 if plan == 'yearly' else 19.99
        invoice = create_crypto_invoice(amount, current_user.id, plan)

        if invoice and 'invoice_url' in invoice:
            print(f"🔗 Redirecting to payment: {invoice['invoice_url']}")
            return redirect(invoice['invoice_url'])
        else:
            flash('Payment gateway error. Please try again or contact support.', 'error')
            return redirect(url_for('pricing'))
    else:
        # LOCAL: Mock payment (testing)
        current_user.is_premium = True
        current_user.plan_type = plan
        if plan == 'yearly':
            current_user.subscription_end = datetime.now() + timedelta(days=365)
            flash('🎉 Yearly Premium Activated! (Test Mode)', 'success')
        else:
            current_user.subscription_end = datetime.now() + timedelta(days=30)
            flash('🎉 Monthly Premium Activated! (Test Mode)', 'success')
        current_user.free_calculations_count = 0
        db.session.commit()
        print(f"✅ Mock Premium activated for {current_user.email} ({plan})")
        return redirect(url_for('dashboard'))

# --- CSV Upload (Monthly=Single, Yearly=Multiple) ---
# --- CSV Upload (Monthly=Single, Yearly=Multiple) - NO PANDAS VERSION ---
# --- CSV Upload (Monthly=Single, Yearly=Multiple) - BUILT-IN CSV VERSION ---
@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if not check_premium():
        flash('⚠️ CSV Upload is for Premium users only. Please upgrade!', 'error')
        return redirect(url_for('pricing'))

    if request.method == 'POST':
        files = request.files.getlist('files')

        # ✅ Monthly Plan: Only 1 file allowed
        if current_user.plan_type == 'monthly' and len(files) > 1:
            flash('⚠️ Monthly plan allows 1 file at a time. Upgrade to Yearly for multiple files!', 'error')
            return redirect(url_for('upload'))

        if not files or all(f.filename == '' for f in files):
            flash('No file selected', 'error')
            return redirect(request.url)

        total_trades = 0

        for file in files:
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                try:
                    file.save(filepath)

                    # ✅ Using built-in csv module (NO pandas needed)
                    import csv
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        reader = csv.DictReader(f)
                        file_trades = 0

                        for row in reader:
                            try:
                                trade_type = str(row.get('Type', '')).strip().upper()
                                fees_str = row.get('Fees', '0') or '0'
                                fees = float(fees_str) if fees_str else 0

                                if trade_type == 'BUY':
                                    file_trades += 1
                                elif trade_type == 'SELL':
                                    total_str = row.get('Total', '0') or '0'
                                    sell_value = float(total_str) if total_str else 0
                                    buy_value = sell_value * 0.8  # Simplified logic

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
                                    file_trades += 1
                            except Exception as row_err:
                                print(f"⚠️ Skipping row: {row_err}")
                                continue

                    total_trades += file_trades
                    print(f"✅ File {filename}: {file_trades} trades processed")

                except Exception as e:
                    print(f"❌ Error processing {filename}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    flash(f'Error in {filename}: {str(e)}', 'error')

        # ✅ Force commit before redirect
        db.session.commit()
        print(f"✅ Total {total_trades} trades committed to database")

        flash(f'🎉 Successfully processed {total_trades} trades!', 'success')
        return redirect(url_for('history'))

    return render_template('upload.html')

# --- Webhook (NowPayments IPN Callback) ---
@app.route('/webhook', methods=['POST'])
def nowpayments_webhook():
    """NowPayments IPN Callback - Verify & Activate Premium"""
    ipn_secret = os.environ.get('IPN_SECRET', '').encode('utf-8')
    received_sig = request.headers.get('x-nowpayments-sig', '')

    data = request.json
    payment_status = data.get('payment_status')
    order_id = data.get('order_id')

    print(f" Webhook received: {payment_status} for order {order_id}")

    if payment_status == 'finished':
        try:
            # Extract user_id and plan from order_id (user_123_yearly_...)
            parts = order_id.split('_')
            user_id = int(parts[1])
            plan = parts[2]

            user = User.query.get(user_id)
            if user:
                user.is_premium = True
                user.plan_type = plan
                if plan == 'yearly':
                    user.subscription_end = datetime.now() + timedelta(days=365)
                else:
                    user.subscription_end = datetime.now() + timedelta(days=30)
                user.free_calculations_count = 0
                db.session.commit()
                print(f"✅ Premium activated for user {user_id} ({plan})")
            else:
                print(f"❌ User {user_id} not found")
        except Exception as e:
            print(f"Webhook Error: {e}")

    return {'status': 'ok'}, 200

# --- Database Create ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
