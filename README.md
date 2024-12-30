# EndoReg DB

EndoReg DB is a **Django-based application** designed for managing and analyzing medical data, particularly in the context of endoscopic procedures. The project includes robust data models, integrations with machine learning tools, and tools for generating reports and visualizations.

---

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Usage](#usage)
5. [Development](#development)
6. [Testing](#testing)
7. [Contributing](#contributing)
8. [License](#license)

---

## Features

- **Data Management**: Structured models for patients, examinations, findings, and more.
- **AI Integration**: Seamless integration with ML models using PyTorch Lightning.
- **Data Import/Export**: Support for importing legacy data and exporting analysis results.
- **Validation**: Robust validation for medical data fields.
- **Visualization**: Generate visual insights from video and medical data.
- **Extensibility**: Modular architecture to add custom rules, integrations, or new models.

---

## Requirements

This project uses Python 3.11 or higher and requires the following dependencies:

- Django >= 5.1.3
- PyTorch Lightning >= 2.4.0
- Pandas, NumPy, and SciPy for data processing
- OpenCV and Tesseract for image and video processing
- PyYAML for configuration

For a complete list, see the `pyproject.toml` file.

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/wg-lux/endoreg-db.git
cd endoreg-db
```

### Step 2: Set Up a Virtual Environment

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Or on Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

### Step 3: Install Dependencies

Install the required Python dependencies:

```bash
pip install -r requirements.txt
```

### Step 4: Run Migrations

Apply the database migrations to set up the schema:

```bash
python manage.py migrate
```

### Step 5: Create a Superuser

Create an admin account to access the Django admin interface:

```bash
python manage.py createsuperuser
```

Follow the prompts to set up a username, email, and password.

### Step 6: Start the Development Server

Run the Django development server:

```bash
python manage.py runserver
```

Open your browser and navigate to `http://localhost:8000` to access the application.

---

## Usage

### Admin Interface

Access the Django admin interface at `/admin` to manage and view data models.

### Data Import

Use the provided management commands to import data into the system. For example:

```bash
python manage.py load_name_data
```

Replace `load_name_data` with the appropriate command for your data.

### Machine Learning Integration

The project includes tools for video preprocessing and analysis using PyTorch models. Use the management commands in the `processing_functions` module to execute these tasks.

---

## Development

### Pre-commit Hooks

This project uses **pre-commit** hooks to enforce code quality.

1. Install pre-commit:

   ```bash
   pip install pre-commit
   ```

2. Set up the hooks:

   ```bash
   pre-commit install
   ```

3. Run hooks manually for all files:

   ```bash
   pre-commit run --all-files
   ```

### Code Style

- **Black**: Automatically formats code.
- **isort**: Ensures consistent import order.
- **flake8**: Identifies Python syntax errors and style issues.

Run the tools locally before committing changes:

```bash
black .
isort .
flake8 .
```

---

## Testing

To run tests and ensure functionality:

```bash
python manage.py test
```

You can also configure continuous integration (CI) to automate testing.

---

## Contributing

Contributions are welcome! To contribute:

1. Fork the repository.
2. Create a new branch (`git checkout -b feature/my-feature`).
3. Commit your changes (`git commit -m "Add new feature"`).
4. Push to the branch (`git push origin feature/my-feature`).
5. Open a pull request.

Make sure your code adheres to the project's coding standards and passes all tests.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.

