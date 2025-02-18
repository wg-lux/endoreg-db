import streamlit as st
import matplotlib.pyplot as plt
import json
from PIL import Image
import argparse


# Load the JSON file
@st.cache_data
def load_data(file_path):
    with open(file_path, 'r') as f:
        data = json.load(f)
    return data

# Streamlit App
def main(file_path):
    st.title("Visualize Predictions")

    # Load the data
    data = load_data(file_path)

    # Dropdown to select label
    selected_label = st.selectbox('Select Label', options=data['labels'])

    # Get the index of the selected label
    label_index = data['labels'].index(selected_label)

    # Extract predictions for the selected label
    selected_predictions = [pred[label_index] for pred in data['predictions']]

    # Line plot for the selected label's prediction values
    st.subheader(f"Line plot for {selected_label}")

    fig, ax = plt.subplots(figsize=(10, 6))  # Explicitly create a figure and axis
    ax.plot(selected_predictions)
    ax.set_xlabel('Frame')
    ax.set_ylabel('Prediction Value')
    ax.set_title(f'Predictions for {selected_label}')

    st.pyplot(fig)  # Pass the figure object explicitly


    # Slider to select frame
    frame_idx = st.slider('Select a frame', 0, len(data['paths']) - 1)

    # Display the frame
    image_path = data['paths'][frame_idx]
    image = Image.open(image_path)
    st.image(image, caption=f"Frame {frame_idx}", use_column_width=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Visualize Predictions")
    parser.add_argument("--file", type=str, help="Path to JSON file containing predictions", required=True)
    args = parser.parse_args()
    main(args.file)