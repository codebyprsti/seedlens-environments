-- Remove redundant column (use extracted_grower only). Safe if column never existed.
ALTER TABLE operations.crop_indices DROP COLUMN IF EXISTS extracted_grower_name;
