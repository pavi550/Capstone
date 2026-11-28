-- MySQL does not support ADD COLUMN IF NOT EXISTS.
SET @cp2_pet_ddl = IF(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = DATABASE() AND table_name = 'pets' AND column_name = 'breed'),
  'DO 0', 'ALTER TABLE pets ADD COLUMN breed VARCHAR(100) CHARACTER SET utf8mb4');
PREPARE cp2_pet_statement FROM @cp2_pet_ddl;
EXECUTE cp2_pet_statement;
DEALLOCATE PREPARE cp2_pet_statement;

SET @cp2_pet_ddl = IF(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema = DATABASE() AND table_name = 'pets' AND column_name = 'weight'),
  'DO 0', 'ALTER TABLE pets ADD COLUMN weight DECIMAL(5, 2)');
PREPARE cp2_pet_statement FROM @cp2_pet_ddl;
EXECUTE cp2_pet_statement;
DEALLOCATE PREPARE cp2_pet_statement;
