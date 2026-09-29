import os
from unidecode import unidecode

def rename_files_to_ascii():
    """
    Renames all .txt files in the current folder by converting filenames with accents into ASCII.
    """
    try:
        # Get the current folder path
        folder_path = os.getcwd()

        # Iterate through all files in the folder
        for filename in os.listdir(folder_path):
            # Process only .txt files
            if filename.endswith('.txt'):
                old_file_path = os.path.join(folder_path, filename)

                # Convert filename to ASCII
                new_filename = unidecode(filename)
                new_file_path = os.path.join(folder_path, new_filename)

                # Rename the file if needed
                if old_file_path != new_file_path:
                    os.rename(old_file_path, new_file_path)
                    print(f"Renamed: '{filename}' -> '{new_filename}'")

        print("Renaming completed.")

    except Exception as e:
        print(f"An error occurred: {e}")

# Example usage
if __name__ == "__main__":
    rename_files_to_ascii()

