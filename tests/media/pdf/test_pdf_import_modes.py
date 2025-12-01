#!/usr/bin/env python3
"""
Test script for the updated PdfImportService with blackening and cropping modes.
"""

import os
import sys
from pathlib import Path

# Add Django project to path
sys.path.insert(0, str(Path(__file__).parent))

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
import django

django.setup()

import logging

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.pdf_import import PdfImportService

logger = logging.getLogger(__name__)


def test_blackening_mode():
    """
    Verify that PdfImportService in blackening mode can access the report reader.
    
    Returns:
        bool: `True` if the report reader (lx_anonymizer) is available and the blackening mode can be tested, `False` otherwise.
    """
    print("\n=== Testing PDF Import with Blackening Mode ===")

    # Create service with blackening mode
    service = PdfImportService.with_blackening(allow_meta_overwrite=True)
    print(f"✅ Created service with processing_mode: {service.processing_mode}")

    # Check if lx_anonymizer is available
    available, reader_class = service._ensure_report_reading_available()
    print(f"📚 ReportReader available: {available}")

    if not available:
        print("❌ Cannot test blackening mode - lx_anonymizer not available")
        return False

    print(f"📚 ReportReader class: {reader_class}")
    return True


def test_cropping_mode():
    """
    Run tests for the PDF import service configured in cropping mode.
    
    Creates a PdfImportService using cropping mode and verifies that the report-reading dependency (lx_anonymizer) is available.
    
    Returns:
        True if the service was created and the required report reader is available, False otherwise.
    """
    print("\n=== Testing PDF Import with Cropping Mode ===")

    # Create service with cropping mode
    service = PdfImportService.with_cropping(allow_meta_overwrite=True)
    print(f"✅ Created service with processing_mode: {service.processing_mode}")

    # Check if lx_anonymizer is available
    available, reader_class = service._ensure_report_reading_available()
    print(f"📚 ReportReader available: {available}")

    if not available:
        print("❌ Cannot test cropping mode - lx_anonymizer not available")
        return False

    print(f"📚 ReportReader class: {reader_class}")
    return True


def test_default_mode():
    """Test the default initialization."""
    print("\n=== Testing Default Service Initialization ===")

    # Create service with default mode (should be blackening)
    service = PdfImportService()
    print(f"✅ Default processing_mode: {service.processing_mode}")

    # Test explicit blackening mode
    service_blackening = PdfImportService(processing_mode="blackening")
    print(f"✅ Explicit blackening mode: {service_blackening.processing_mode}")

    # Test explicit cropping mode
    service_cropping = PdfImportService(processing_mode="cropping")
    print(f"✅ Explicit cropping mode: {service_cropping.processing_mode}")

    return True


def test_invalid_mode():
    """Test invalid processing mode handling."""
    print("\n=== Testing Invalid Mode Handling ===")

    try:
        service = PdfImportService(processing_mode="invalid_mode")
        print("❌ Should have raised ValueError for invalid mode")
        return False
    except ValueError as e:
        print(f"✅ Correctly raised ValueError: {e}")
        return True
    except Exception as e:
        print(f"❌ Unexpected exception: {e}")
        return False


def test_method_signatures():
    """Test the new processing methods exist."""
    print("\n=== Testing Method Signatures ===")

    service = PdfImportService.with_blackening()

    # Check if new methods exist
    methods_to_check = ["_process_with_blackening", "_process_with_cropping"]

    for method_name in methods_to_check:
        if hasattr(service, method_name):
            print(f"✅ Method {method_name} exists")
        else:
            print(f"❌ Method {method_name} missing")
            return False

    return True


def main():
    """
    Execute the suite of PdfImportService tests and print a pass/fail summary.
    
    Runs each predefined test (default/invalid modes, method signatures, blackening, cropping),
    prints individual test outcomes and a condensed summary, and returns whether all tests passed.
    
    Returns:
        bool: True if all tests passed, False otherwise.
    """
    print("🧪 Testing Updated PdfImportService")
    print("=" * 50)

    tests = [
        ("Default Mode", test_default_mode),
        ("Invalid Mode", test_invalid_mode),
        ("Method Signatures", test_method_signatures),
        ("Blackening Mode", test_blackening_mode),
        ("Cropping Mode", test_cropping_mode),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
            status = "✅ PASSED" if result else "❌ FAILED"
            print(f"\n{status}: {test_name}")
        except Exception as e:
            results.append((test_name, False))
            print(f"\n❌ FAILED: {test_name} - Exception: {e}")

    print("\n" + "=" * 50)
    print("📊 Test Summary:")
    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {test_name}")

    print(f"\n🎯 Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! PdfImportService is ready with both processing modes.")
    else:
        print("⚠️  Some tests failed. Check the output above for details.")

    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)