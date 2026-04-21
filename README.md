## T-SQL queries used during the project

### Create Tables
Query that create Staging Tables, Dimension Table, Fact Sales and Indexes for some columns

```
-- ================================================================
-- 1. STAGING TABLE: Raw data landing zone from Azure Blob Storage
-- ================================================================
CREATE TABLE Staging_RetailData (
    InvoiceNo VARCHAR(20) NOT NULL,
    StockCode VARCHAR(20) NOT NULL,
    Description NVARCHAR(255),
    Quantity INT,
    InvoiceDate DATETIME2 NOT NULL,  -- Better precision & storage than DATETIME
    UnitPrice DECIMAL(18, 4),
    CustomerID INT,
    Country NVARCHAR(50),  -- Changed to NVARCHAR for international characters
    TotalPrice DECIMAL(18, 4)
);
 
-- ================================================================
-- 2. DIMENSION TABLE: Customer master data
-- ================================================================
CREATE TABLE DimCustomer (
    CustomerID INT PRIMARY KEY,
    Country NVARCHAR(50) NOT NULL,
    FirstPurchaseDate DATETIME2,  -- Add this now for future use
    LastPurchaseDate DATETIME2,
    TotalPurchases INT DEFAULT 0,
    
    -- Audit columns (best practice)
    CreatedDate DATETIME2 DEFAULT GETDATE(),
    ModifiedDate DATETIME2 DEFAULT GETDATE()
);
 
-- Create index for country-based queries (heatmap filtering)
CREATE NONCLUSTERED INDEX IX_DimCustomer_Country 
ON DimCustomer(Country);
 
-- ================================================================
-- 3. FACT TABLE: Sales transactions (main analytics table)
-- ================================================================
CREATE TABLE FactSales (
    SalesKey INT IDENTITY(1,1) PRIMARY KEY,
    InvoiceNo VARCHAR(20) NOT NULL,
    CustomerID INT NOT NULL,  -- Good that you made this NOT NULL
    StockCode VARCHAR(20) NOT NULL,
    Quantity INT NOT NULL,
    UnitPrice DECIMAL(18, 4) NOT NULL,
    TotalPrice DECIMAL(18, 4) NOT NULL,
    InvoiceDate DATETIME2 NOT NULL,
    
    -- Foreign key to ensure data integrity
    CONSTRAINT FK_FactSales_DimCustomer 
        FOREIGN KEY (CustomerID) REFERENCES DimCustomer(CustomerID)
);
 
-- Performance indexes
CREATE NONCLUSTERED INDEX IX_FactSales_CustomerID 
ON FactSales(CustomerID);
 
CREATE NONCLUSTERED INDEX IX_FactSales_InvoiceDate 
ON FactSales(InvoiceDate);
 
CREATE NONCLUSTERED INDEX IX_FactSales_StockCode 
ON FactSales(StockCode);
 
-- Composite index for common query patterns (date + customer analysis)
CREATE NONCLUSTERED INDEX IX_FactSales_Date_Customer 
ON FactSales(InvoiceDate, CustomerID) INCLUDE (TotalPrice);
```

### Query that checks the total of rows loaded
```
SELECT COUNT(*) FROM Staging_RetailData
```

### Populating DimCustomer Table

We first disabled the foreign key constraints on the DimCustomer table and deleted its existing data, since we couldn’t populate new records while the constraints were active.
After that, we inserted deduplicated customer data from Staging_RetailData, grouping by CustomerID and selecting the corresponding Country.
Once the data was successfully inserted, we re-enabled the foreign key constraints.
Finally, we verified that the data was populated correctly, and the transaction completed successfully.


```
BEGIN TRANSACTION;
 
-- Disable foreign keys
ALTER TABLE DimCustomer NOCHECK CONSTRAINT ALL;
 
-- Clear the table
DELETE FROM DimCustomer;
 
-- Insert with proper deduplication
INSERT INTO DimCustomer (CustomerID, Country)
SELECT 
    CustomerID,
    MIN(Country) AS Country  -- Choose one country per customer
FROM Staging_RetailData
WHERE CustomerID IS NOT NULL
  AND Country IS NOT NULL
GROUP BY CustomerID;
 
-- Re-enable foreign keys
ALTER TABLE DimCustomer CHECK CONSTRAINT ALL;
 
-- Verify
SELECT COUNT(*) AS TotalRecords,
       COUNT(DISTINCT CustomerID) AS UniqueCustomers
FROM DimCustomer;
 
COMMIT TRANSACTION;
-- If error: ROLLBACK TRANSACTION;
```

### Populating FactSales table
We limited it to 3000 rows because the request to get 300k was timeout out, hence the total transactions showing in the results are 3000 rows.

```
-- Populate Fact Table for Sales (LIMITED to 3000 rows)
INSERT INTO FactSales (InvoiceNo, CustomerID, StockCode, Quantity, UnitPrice, TotalPrice, InvoiceDate)
SELECT TOP 3000  -- <-- This limits the rows loaded
    InvoiceNo,
    CustomerID,
    StockCode,
    Quantity,
    UnitPrice,
    TotalPrice,
    InvoiceDate
FROM
    Staging_RetailData
WHERE
    CustomerID IS NOT NULL;
 
-- Verification: Check the total transaction count (should be 3000)
SELECT COUNT(*) AS Total_Transactions FROM FactSales;
```