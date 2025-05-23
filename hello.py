import torch

# Define a function which creates a file (YYYY-MM-DD_HH-MM-SS.txt) in the directory "./data" and writes a message to it


def write_message(message=None):
    import os
    import datetime

    # Create a directory if it does not exist
    os.makedirs("data", exist_ok=True)

    if not message:
        # print whether cuda is available, device name, total memory and multiprocessor count
        message = "EndoReg DB DevEnv (with cuda support)!\n"
        message += f"Cuda is available: {torch.cuda.is_available()}\n"
        message += f"Number of usable devices: {torch.cuda.device_count()}\n"
        message += "\n"

        for i in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(i)
            message += f"Device {i}: {properties.name}\n"
            message += f"  Total Memory: {properties.total_memory / (1024**2):.2f} MB\n"
            message += f"  Multiprocessor Count: {properties.multi_processor_count}\n"
            message += "\n"


def main():
    print("Hello from EndoReg-DB DevEnv (with cuda support)!")
    print("Cuda is available:", torch.cuda.is_available())

    num_of_gpus = torch.cuda.device_count()
    print("Number of usable devices:", num_of_gpus)

    for i in range(num_of_gpus):
        properties = torch.cuda.get_device_properties(i)
        print(f"Device {i}: {properties.name}")
        print(f"  Total Memory: {properties.total_memory / (1024**2):.2f} MB")
        print(f"  Multiprocessor Count: {properties.multi_processor_count}")

    write_message()

    # write_message()


if __name__ == "__main__":
    main()
