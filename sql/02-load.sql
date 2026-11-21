SET XACT_ABORT ON;
BEGIN TRANSACTION;

DELETE FROM dbo.Stock;
DELETE FROM dbo.Sales;
DELETE FROM dbo.Calendar;
DELETE FROM dbo.Product;
DELETE FROM dbo.Category;
DELETE FROM dbo.Store;
DELETE FROM dbo.Region;

-- On Linux, RAW preserves UTF-8 bytes when the target is varchar.
-- The UTF-8 collation then decodes them on insertion into the Unicode columns.
CREATE TABLE #RegionCsv (RegionId int, RegionName varchar(320) COLLATE Latin1_General_100_CI_AS_SC_UTF8);
CREATE TABLE #StoreCsv (StoreId int, RegionId int, StoreName varchar(320) COLLATE Latin1_General_100_CI_AS_SC_UTF8);
CREATE TABLE #CategoryCsv (CategoryId int, CategoryName varchar(320) COLLATE Latin1_General_100_CI_AS_SC_UTF8);
CREATE TABLE #ProductCsv (ProductId int, CategoryId int, ProductName varchar(320) COLLATE Latin1_General_100_CI_AS_SC_UTF8);

BULK INSERT #RegionCsv FROM '/var/opt/mssql/import/Region.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
INSERT INTO dbo.Region (RegionId, RegionName) SELECT RegionId, RegionName FROM #RegionCsv;
BULK INSERT #StoreCsv FROM '/var/opt/mssql/import/Store.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
INSERT INTO dbo.Store (StoreId, RegionId, StoreName) SELECT StoreId, RegionId, StoreName FROM #StoreCsv;
BULK INSERT #CategoryCsv FROM '/var/opt/mssql/import/Category.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
INSERT INTO dbo.Category (CategoryId, CategoryName) SELECT CategoryId, CategoryName FROM #CategoryCsv;
BULK INSERT #ProductCsv FROM '/var/opt/mssql/import/Product.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
INSERT INTO dbo.Product (ProductId, CategoryId, ProductName) SELECT ProductId, CategoryId, ProductName FROM #ProductCsv;
DROP TABLE #RegionCsv, #StoreCsv, #CategoryCsv, #ProductCsv;

BULK INSERT dbo.Calendar FROM '/var/opt/mssql/import/Calendar.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
BULK INSERT dbo.Sales FROM '/var/opt/mssql/import/Sales.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);
BULK INSERT dbo.Stock FROM '/var/opt/mssql/import/Stock.csv'
WITH (FORMAT = 'CSV', FIRSTROW = 2, CODEPAGE = 'RAW', ROWTERMINATOR = '0x0a', CHECK_CONSTRAINTS);

COMMIT TRANSACTION;
