import exiftool

def set_metadata(filepath, categories, dry_run=False):

    args = []
    writable_categories = { "EXIF", "IPTC","XMP"}

    for category, metadata in categories.items():
        if category not in writable_categories:
            continue

        for tag, value in metadata.items():
            if value is None:
                continue

            if isinstance(value, list):
                for item in value:
                    args.append(
                        f"-{category}:{tag}={item}".encode("utf-8")
                    )
            else:
                args.append(
                    f"-{category}:{tag}={value}".encode("utf-8")
                )

    if dry_run:
        print(f"Dry run: Metadata to be set for {filepath}:")
        for arg in args:
            print(arg.decode("utf-8"))
    else:
        with exiftool.ExifTool() as et:
            et.execute(
                 b"-overwrite_original",
                *args,
                filepath.encode("utf-8")
            )
