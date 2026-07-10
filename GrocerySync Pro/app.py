from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# ==========================================
# DATABASE ARCHITECTURE (Relational)
# ==========================================

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    is_perishable = db.Column(db.Boolean, default=True)
    standard_shelf_life_days = db.Column(db.Integer, nullable=False)
    min_stock_level = db.Column(db.Integer, default=20)
    is_active = db.Column(db.Boolean, default=True)

    batches = db.relationship('Batch', backref='product', lazy=True)


class Batch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    base_price = db.Column(db.Float, nullable=False)
    received_date = db.Column(db.DateTime, default=datetime.utcnow)
    expiry_date = db.Column(db.DateTime, nullable=False)

    @property
    def hours_to_live(self):
        if not self.product.is_perishable:
            return 999999
        delta = self.expiry_date - datetime.utcnow()
        return max(0, delta.total_seconds() / 3600)

    @property
    def days_to_live(self):
        return round(self.hours_to_live / 24, 1)

    @property
    def status(self):
        if not self.product.is_perishable:
            return "Safe (Non-Perishable)"
        hours = self.hours_to_live
        if hours <= 0:
            return "Expired"
        elif hours <= 48:
            return "Expiring Soon"
        return "Safe"

    @property
    def current_price(self):
        if not self.product.is_perishable:
            return self.base_price
        hours = self.hours_to_live
        if hours <= 0:
            return 0.00
        elif hours <= 12:
            return self.base_price * 0.50
        elif hours <= 48:
            return self.base_price * 0.75
        return self.base_price


# ==========================================
# SEED DATA
# ==========================================
def seed_database():
    if Product.query.first() is None:
        products = [
            Product(sku="DAI-001", name="Nandini Toned Milk 500ml", category="Dairy", is_perishable=True,
                    standard_shelf_life_days=2, min_stock_level=50),
            Product(sku="DAI-002", name="Milky Mist Paneer 200g", category="Dairy", is_perishable=True,
                    standard_shelf_life_days=15, min_stock_level=20),
            Product(sku="PRO-001", name="Fresh Tomatoes (Local)", category="Produce", is_perishable=True,
                    standard_shelf_life_days=7, min_stock_level=30),
            Product(sku="PRO-002", name="Ooty Carrots", category="Produce", is_perishable=True,
                    standard_shelf_life_days=10, min_stock_level=15),
            Product(sku="MEA-001", name="Fresh Chicken Breast", category="Meat", is_perishable=True,
                    standard_shelf_life_days=3, min_stock_level=10),
            Product(sku="BAK-001", name="Modern Whole Wheat Bread", category="Bakery", is_perishable=True,
                    standard_shelf_life_days=5, min_stock_level=25),
            Product(sku="GRO-001", name="Aashirvaad Atta 5kg", category="Grocery", is_perishable=False,
                    standard_shelf_life_days=180, min_stock_level=10),
            Product(sku="GRO-002", name="Tata Salt 1kg", category="Grocery", is_perishable=False,
                    standard_shelf_life_days=365, min_stock_level=10),
        ]
        db.session.bulk_save_objects(products)
        db.session.commit()


# ==========================================
# ROUTING LOGIC
# ==========================================
@app.route('/')
def dashboard():
    products = Product.query.filter_by(is_active=True).all()
    batches = Batch.query.all()
    active_batches = [b for b in batches if b.product.is_active]

    risk_data = {"Safe": 0, "Safe (Non-Perishable)": 0, "Expiring Soon": 0, "Expired": 0}
    shrinkage_data = {"Dairy": 0, "Meat": 0, "Produce": 0, "Bakery": 0, "Grocery": 0}
    total_recovered = 0.0

    restock_alerts = []
    total_products = len(products)
    healthy_products = 0

    for prod in products:
        safe_qty = sum(b.quantity for b in prod.batches if b.status != "Expired")
        if safe_qty < prod.min_stock_level:
            restock_alerts.append({
                'id': prod.id,
                'name': prod.name,
                'current': safe_qty,
                'min': prod.min_stock_level,
                'needed': prod.min_stock_level - safe_qty
            })
        else:
            healthy_products += 1

    for batch in batches:
        if batch.status == "Expired":
            if batch.product.category in shrinkage_data:
                shrinkage_data[batch.product.category] += (batch.base_price * batch.quantity)
        if batch.status == "Expiring Soon":
            total_recovered += (batch.current_price * batch.quantity)

        if batch.product.is_active:
            risk_data[batch.status] += 1

    total_shrinkage = sum(shrinkage_data.values())
    formatted_recovery = f"₹ {total_recovered:,.2f}/-"
    stock_health_score = round((healthy_products / total_products) * 100) if total_products > 0 else 0

    return render_template(
        'dashboard.html',
        products=products,
        batches=active_batches,
        restock_alerts=restock_alerts,
        stock_health=stock_health_score,
        total_tracked=len(active_batches),
        total_recovered=formatted_recovery,
        total_recovered_raw=total_recovered,
        total_shrinkage_raw=total_shrinkage,
        risk_labels=list(risk_data.keys()),
        risk_values=list(risk_data.values()),
        shrinkage_labels=list(shrinkage_data.keys()),
        shrinkage_values=list(shrinkage_data.values())
    )


@app.route('/add_batch', methods=['POST'])
def add_batch():
    if request.method == 'POST':
        product_id = request.form.get('product_id')
        base_price = request.form.get('base_price')
        quantity = request.form.get('quantity')
        expiry_date_str = request.form.get('expiry_date')

        try:
            parsed_date = datetime.strptime(expiry_date_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            parsed_date = datetime.strptime(expiry_date_str, '%Y-%m-%dT%H:%M:%S')

        new_batch = Batch(
            product_id=int(product_id),
            base_price=float(base_price),
            quantity=int(quantity),
            expiry_date=parsed_date
        )

        db.session.add(new_batch)
        db.session.commit()
        return redirect(url_for('dashboard'))


@app.route('/add_product', methods=['POST'])
def add_product():
    if request.method == 'POST':
        sku = request.form.get('sku').upper()
        name = request.form.get('name')
        category = request.form.get('category')
        is_perishable = request.form.get('is_perishable') == 'true'
        shelf_life = request.form.get('shelf_life')
        min_stock = request.form.get('min_stock')

        valid_prefixes = ['GRO', 'DAI', 'PRO', 'MEA', 'BAK']
        prefix = sku[:3] if len(sku) >= 3 else ""
        if prefix not in valid_prefixes:
            return redirect(
                url_for('dashboard', format_error='true', temp_sku=sku, temp_name=name, temp_life=shelf_life,
                        temp_min=min_stock))

        existing_product = Product.query.filter_by(sku=sku).first()
        if existing_product:
            return redirect(
                url_for('dashboard', duplicate_error='true', temp_sku=sku, temp_name=name, temp_life=shelf_life,
                        temp_min=min_stock))

        new_product = Product(
            sku=sku,
            name=name.title(),
            category=category,
            is_perishable=is_perishable,
            standard_shelf_life_days=int(shelf_life) if shelf_life else 0,
            min_stock_level=int(min_stock) if min_stock else 0,
            is_active=True
        )

        try:
            db.session.add(new_product)
            db.session.commit()
        except:
            db.session.rollback()

        return redirect(url_for('dashboard') + '?product_added=true')


@app.route('/delete_batch/<int:batch_id>', methods=['POST'])
def delete_batch(batch_id):
    batch_to_delete = Batch.query.get_or_404(batch_id)
    db.session.delete(batch_to_delete)
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/archive_product/<int:product_id>', methods=['POST'])
def archive_product(product_id):
    product_to_archive = Product.query.get_or_404(product_id)
    product_to_archive.is_active = False
    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/print_tag/<int:batch_id>')
def print_tag(batch_id):
    batch = Batch.query.get_or_404(batch_id)
    return render_template('print_tag.html', batch=batch)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        seed_database()
    app.run(debug=True)